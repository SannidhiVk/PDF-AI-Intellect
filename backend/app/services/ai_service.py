"""
ai_service.py
-------------
Wraps all LLM interactions:
  - Google Gemini embeddings (`gemini-embedding-001`, truncated to 768 dims via
    output_dimensionality) for vector search.
  - Groq Cloud API (`llama-3.3-70b-versatile`) for fast AI Summaries and RAG Chat.

Note: Embeddings were previously attempted via Groq's cloud API
(`nomic-embed-text-v1_5`) — which is not exposed on standard accounts — and
via a paid-only third-party embedding model. Embeddings now go through
Google's Gemini API, which offers a genuine free tier and supports
`output_dimensionality=768` natively (Matryoshka Representation Learning),
so the output vector matches the existing Supabase pgvector column
(`vector(768)`) with zero schema migration required.

RATE LIMIT HANDLING:
`gemini-embedding-001` has a fairly low free-tier requests-per-minute quota.
All embedding calls below are wrapped with exponential-backoff retry (via
`tenacity`) so transient 429 RESOURCE_EXHAUSTED errors are absorbed
automatically instead of failing the whole request. Only 429/RESOURCE_EXHAUSTED
errors are retried — auth errors, invalid input, etc. fail immediately since
retrying won't help.
"""

import os
import textwrap

from groq import Groq
from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from dotenv import load_dotenv
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
    before_sleep_log,
)
import logging

load_dotenv()
load_dotenv("backend/.env")

logger = logging.getLogger("ai_service")

# ── Groq Connection (Summaries & RAG Chat only) ─────────────────────────────
_GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
_groq_client = Groq(api_key=_GROQ_API_KEY) if _GROQ_API_KEY else None
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

# ── Gemini Connection (Embeddings only) ─────────────────────────────────────
_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
_gemini_client = genai.Client(api_key=_GEMINI_API_KEY) if _GEMINI_API_KEY else None
EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001").strip()
EMBEDDING_DIMENSION = 768  # matches existing Supabase pgvector column — do not change without a migration


# ── Retry helper: only retry on 429 / RESOURCE_EXHAUSTED ────────────────────

def _is_rate_limit_error(exc: BaseException) -> bool:
    """Return True only for quota/rate-limit errors — not auth or bad-input errors."""
    msg = str(exc)
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        if code == 429:
            return True
    return "RESOURCE_EXHAUSTED" in msg or "429" in msg


_embedding_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception(_is_rate_limit_error),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)


@_embedding_retry
def _embed_call(client: genai.Client, model: str, contents, config: types.EmbedContentConfig):
    """Low-level Gemini embed_content call, wrapped with backoff retry on 429s."""
    return client.models.embed_content(model=model, contents=contents, config=config)


# ── Embedding Functions (Gemini) ─────────────────────────────────────────────
#
# gemini-embedding-001 supports the `output_dimensionality` param to truncate
# its native 3072-dim output down to a smaller vector (Matryoshka-style), so
# we request 768 to stay compatible with the existing pgvector schema. Google
# auto-normalizes truncated dimensions, so no manual re-normalization step is
# needed. Raw text is passed as-is with no task-instruction prefix required.

def _require_gemini_client() -> genai.Client:
    if not _gemini_client:
        raise RuntimeError(
            "GEMINI_API_KEY is missing from your .env file (or Render's Environment tab). "
            "Embeddings cannot be generated without it."
        )
    return _gemini_client


def generate_embedding(text: str) -> list[float]:
    """Generate a 768-dim vector embedding for a single text chunk."""
    client = _require_gemini_client()
    try:
        response = _embed_call(
            client,
            EMBEDDING_MODEL,
            text,
            types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSION),
        )
        return response.embeddings[0].values
    except Exception as exc:
        raise RuntimeError(f"Gemini embedding error: {exc}") from exc


def generate_embeddings_batch(chunks: list[str]) -> list[list[float]]:
    """Generate embeddings for a list of text chunks in a single batched call.

    Always prefer this over calling generate_embedding() in a loop — one batched
    request uses far less of the per-minute quota than N individual requests.
    """
    if not chunks:
        return []

    client = _require_gemini_client()

    try:
        response = _embed_call(
            client,
            EMBEDDING_MODEL,
            chunks,
            types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSION),
        )
        return [item.values for item in response.embeddings]
    except Exception as exc:
        raise RuntimeError(f"Gemini batch embedding error: {exc}") from exc


def generate_query_embedding(query: str) -> list[float]:
    """Generate an embedding vector for a user search query."""
    client = _require_gemini_client()
    try:
        response = _embed_call(
            client,
            EMBEDDING_MODEL,
            query,
            types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSION),
        )
        return response.embeddings[0].values
    except Exception as exc:
        raise RuntimeError(f"Gemini query embedding error: {exc}") from exc


# ── PDF Summarisation (Groq API) ───────────────────────────────────────────

_SUMMARY_SYSTEM_PROMPT = textwrap.dedent("""\
    You are an expert document analyst producing a clean, disciplined executive summary.

    FORMATTING & LAYOUT RULES:
    - Never open with conversational filler like "Based on the provided text..." or "Here is the summary...". Start directly with the section headers.
    - Use standard markdown bullet points (`- `).
    - Always **bold key terms**, topic headers, dates, metrics, and project names for maximum readability.
    - Put an empty line between each bullet point so list items do not visually bunch up.

    GROUNDING RULES:
    - Use ONLY facts explicitly stated in the document.
    - If a section (like Key Data & Metrics) has no relevant content in the document, omit that section entirely rather than writing filler.

    Format your output EXACTLY as follows:

    ### 📄 Executive Summary

    **Overview:**
    <2-3 clear, direct sentences explaining what this document is, who it concerns, and its main purpose>

    **Key Topics Covered:**
    - **<Topic 1>**: <Brief 1-line description>

    - **<Topic 2>**: <Brief 1-line description>

    - **<Topic 3>**: <Brief 1-line description>

    **Key Takeaways & Findings:**
    - **<Takeaway 1>**: <Concrete decision, finding, or recommendation>

    - **<Takeaway 2>**: <Concrete decision, finding, or recommendation>

    **Key Data & Figures:**
    - **<Metric/Date/Name>**: <Relevant context>

    - **<Metric/Date/Name>**: <Relevant context>

    **Document Type:** **<e.g. Resume / Project Report / Manual / Contract>**
""")


def generate_summary(text: str) -> str:
    """Generate a structured summary using the Groq API."""
    if not _groq_client:
        raise RuntimeError("GROQ_API_KEY is missing from your .env file.")

    MAX_SUMMARY_CHARS = 30_000
    truncated_text = text[:MAX_SUMMARY_CHARS]

    try:
        response = _groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Please summarise the following document:\n\n{truncated_text}",
                },
            ],
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        raise RuntimeError(f"Groq API Summarization Error: {exc}") from exc


# ── RAG PDF Chat (Groq API) ────────────────────────────────────────────────

_RAG_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a precise, laser-focused document assistant. Your sole job is to answer
    the user's specific question directly using ONLY the provided context.

    CRITICAL RESPONSE RULES (DO NOT VIOLATE):
    - Answer ONLY what was specifically asked. Do NOT summarize or dump unrelated information from the document.
    - Start immediately with the direct answer. Never output conversational filler like "Based on the context..." or "Here is the answer...".
    - Be highly concise: provide direct facts without fluff or restating the question.
    - Always **bold key keywords**, metrics, dates, project names, and direct answers for maximum scannability.
    - If listing items, list ONLY the items asked for using clean markdown bullet points (`- `). Leave an empty line between bullets.
    - Never add concluding summary paragraphs, closing remarks, or extra commentary at the end.

    GROUNDING RULES:
    - Base your answer strictly on the provided context chunks.
    - If the context does not contain the answer to the specific question, state strictly: "I couldn't find a direct answer to your question in this document."
""")


def generate_rag_answer(
    question: str,
    context_chunks: list[str],
    chat_history: list[dict] | None = None,
) -> str:
    """Generate a grounded answer using Groq based on retrieved document chunks."""
    if not _groq_client:
        raise RuntimeError("GROQ_API_KEY is missing from your .env file.")

    if not context_chunks:
        return "I couldn't find any relevant content in this document to answer your question."

    # Format context chunks
    context_block = "\n\n---\n\n".join(
        f"[Chunk {i + 1}]:\n{chunk}"
        for i, chunk in enumerate(context_chunks)
    )

    # Initialize messages array natively with system prompt
    messages = [{"role": "system", "content": _RAG_SYSTEM_PROMPT}]

    # Append recent chat history turns
    if chat_history:
        for msg in chat_history[-6:]:
            role = msg.get("role", "user")
            messages.append({"role": role, "content": msg["content"]})

    # Prepare current turn with context and user question
    current_prompt = textwrap.dedent(f"""\
        CONTEXT FROM DOCUMENT:
        {context_block}

        USER QUESTION:
        {question}

        Answer the user question directly based solely on the document context above.
    """)

    messages.append({"role": "user", "content": current_prompt})

    try:
        response = _groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        raise RuntimeError(f"Groq API Chat Error: {exc}") from exc
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

# Load .env here as well so this module works correctly when imported
# independently (e.g. unit tests, scripts) without going through main.py.
load_dotenv()
load_dotenv("backend/.env")

logger = logging.getLogger("ai_service")

# ── Groq Connection (Summaries & RAG Chat only) ─────────────────────────────
# The Groq client is instantiated once at module load time.
# It is used ONLY for text generation (summarize + RAG chat), NOT for embeddings.
_GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
_groq_client = Groq(api_key=_GROQ_API_KEY) if _GROQ_API_KEY else None
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

# ── Gemini Connection (Embeddings only) ─────────────────────────────────────
# The Gemini client is instantiated once at module load time.
# It is used ONLY for generating embedding vectors, NOT for text generation.
_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
_gemini_client = genai.Client(api_key=_GEMINI_API_KEY) if _GEMINI_API_KEY else None
EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001").strip()
# 768 dims matches the Supabase pgvector column (`vector(768)`).
# Gemini natively outputs 3072 dims but truncates cleanly to 768 via
# Matryoshka Representation Learning — no manual re-normalization needed.
EMBEDDING_DIMENSION = 768  # matches existing Supabase pgvector column — do not change without a migration


# ── Retry helper: only retry on 429 / RESOURCE_EXHAUSTED ────────────────────

def _is_rate_limit_error(exc: BaseException) -> bool:
    """
    Return True only for quota/rate-limit errors — not auth or bad-input errors.

    tenacity's retry_if_exception() calls this predicate on every exception.
    Returning False causes tenacity to re-raise the error immediately (no retry).
    Returning True causes tenacity to wait and retry up to stop_after_attempt(5).

    We narrow the check to HTTP 429 / RESOURCE_EXHAUSTED so that genuine errors
    (wrong key, bad input, network DNS failure) fail fast instead of wasting
    ~2 minutes of exponential backoff attempts.
    """
    msg = str(exc)
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        if code == 429:
            return True
    return "RESOURCE_EXHAUSTED" in msg or "429" in msg


# Retry decorator applied to the raw Gemini embed_content call.
# Strategy: up to 5 attempts, exponential backoff starting at 2 s, capped at 60 s.
# before_sleep logs a WARNING each time the call is retried so we can see
# rate-limit pressure in the server logs without crashing.
_embedding_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception(_is_rate_limit_error),
    reraise=True,          # after all attempts exhausted, re-raise the original error
    before_sleep=before_sleep_log(logger, logging.WARNING),
)


@_embedding_retry
def _embed_call(client: genai.Client, model: str, contents, config: types.EmbedContentConfig):
    """
    Low-level Gemini embed_content call, wrapped with backoff retry on 429s.

    This thin wrapper exists so the @_embedding_retry decorator can be applied
    to only the network call itself — not to the argument-processing logic in
    generate_embedding() / generate_embeddings_batch() / generate_query_embedding().

    Args:
        client:   The initialised Gemini genai.Client instance.
        model:    Model ID string (e.g. "gemini-embedding-001").
        contents: A single string OR a list of strings to embed.
        config:   EmbedContentConfig specifying output_dimensionality (768).

    Returns:
        The raw Gemini EmbedContentResponse object.
    """
    return client.models.embed_content(model=model, contents=contents, config=config)


# ── Embedding Functions (Gemini) ─────────────────────────────────────────────
#
# gemini-embedding-001 supports the `output_dimensionality` param to truncate
# its native 3072-dim output down to a smaller vector (Matryoshka-style), so
# we request 768 to stay compatible with the existing pgvector schema. Google
# auto-normalizes truncated dimensions, so no manual re-normalization step is
# needed. Raw text is passed as-is with no task-instruction prefix required.

def _require_gemini_client() -> genai.Client:
    """
    Guard: raise a descriptive RuntimeError if the Gemini client was never
    initialized (i.e. GEMINI_API_KEY was not set at startup).

    Called at the top of every embedding function so the developer gets a
    clear message instead of an AttributeError on None.
    """
    if not _gemini_client:
        raise RuntimeError(
            "GEMINI_API_KEY is missing from your .env file (or Render's Environment tab). "
            "Embeddings cannot be generated without it."
        )
    return _gemini_client


def generate_embedding(text: str) -> list[float]:
    """
    Generate a 768-dim vector embedding for a single text chunk.

    Used by callers that need exactly one embedding (e.g. ad-hoc lookups).
    For bulk ingestion, prefer generate_embeddings_batch() — one batched
    call uses far less API quota than N sequential calls here.

    Returns:
        A list of 768 floats representing the text in embedding space.

    Raises:
        RuntimeError: Wraps any Gemini API error with a descriptive message.
    """
    client = _require_gemini_client()
    try:
        # Single-string call — Gemini returns a list of one embedding object.
        response = _embed_call(
            client,
            EMBEDDING_MODEL,
            text,
            types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSION),
        )
        # response.embeddings[0].values is a list[float] of length EMBEDDING_DIMENSION.
        return response.embeddings[0].values
    except Exception as exc:
        raise RuntimeError(f"Gemini embedding error: {exc}") from exc


def generate_embeddings_batch(chunks: list[str]) -> list[list[float]]:
    """
    Generate embeddings for a list of text chunks in a single batched call.

    Always prefer this over calling generate_embedding() in a loop — one batched
    request uses far less of the per-minute quota than N individual requests.

    This is called by _process_pdf_file_contents() immediately after chunking
    to embed every chunk of a PDF in one network round-trip.

    Args:
        chunks: List of text strings (one per document chunk).

    Returns:
        Parallel list of 768-dim embedding vectors — same order as input.

    Raises:
        RuntimeError: Wraps any Gemini API error with a descriptive message.
    """
    if not chunks:
        # Short-circuit: nothing to embed.
        return []

    client = _require_gemini_client()

    try:
        # Passing a list to embed_content batches all strings into one API call.
        # response.embeddings is a list of EmbeddingObject, one per input chunk.
        response = _embed_call(
            client,
            EMBEDDING_MODEL,
            chunks,
            types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSION),
        )
        # Extract the float vector from each EmbeddingObject, preserving order.
        return [item.values for item in response.embeddings]
    except Exception as exc:
        raise RuntimeError(f"Gemini batch embedding error: {exc}") from exc


def generate_query_embedding(query: str) -> list[float]:
    """
    Generate an embedding vector for a user search query.

    Called from main.py's /api/chat endpoint right before the vector similarity
    search in Supabase. The returned vector is compared (cosine similarity) against
    all stored chunk embeddings to find the most relevant context passages.

    Args:
        query: The user's raw question string.

    Returns:
        A 768-dim float list representing the query in the same embedding space
        as the stored document chunk vectors.

    Raises:
        RuntimeError: Wraps any Gemini API error with a descriptive message.
    """
    client = _require_gemini_client()
    try:
        # Identical call to generate_embedding() — kept as a separate function
        # so callsites clearly signal "this is a query, not a document chunk."
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

# The system prompt is defined once at module level (not inside the function)
# to avoid reconstructing the string on every call. textwrap.dedent strips
# the leading spaces introduced by Python's triple-quote indentation.
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
    """
    Generate a structured executive summary of the given text using the Groq API.

    Called from:
      - _process_pdf_file_contents() immediately after chunking (during upload).
      - The /api/summarize endpoint when the user explicitly requests re-summarization.

    Args:
        text: The full extracted text of the PDF (or any document body).

    Returns:
        The markdown-formatted summary string returned by the LLM.

    Raises:
        RuntimeError: If GROQ_API_KEY is missing, or if the API call fails.
    """
    if not _groq_client:
        raise RuntimeError("GROQ_API_KEY is missing from your .env file.")

    # Cap input at 30,000 chars to stay well within the model's context window
    # and keep latency predictable. Text beyond this limit is silently dropped;
    # for most documents the most important content is near the beginning.
    MAX_SUMMARY_CHARS = 30_000
    truncated_text = text[:MAX_SUMMARY_CHARS]

    try:
        # Two-message conversation: system prompt sets the output format,
        # user message provides the raw document text to analyse.
        response = _groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Please summarise the following document:\n\n{truncated_text}",
                },
            ],
            # Low temperature (0.2) to keep the summary factual and consistent
            # across repeated calls on the same document.
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        raise RuntimeError(f"Groq API Summarization Error: {exc}") from exc


# ── RAG PDF Chat (Groq API) ────────────────────────────────────────────────

# System prompt for retrieval-augmented generation chat.
# Key rules enforced here:
#   - Answer ONLY what was asked (prevent information dump).
#   - Attribute facts to their source document (critical for multi-doc chats).
#   - If context doesn't contain the answer, say so explicitly.
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

    MULTI-DOCUMENT RULE (applies whenever the context below contains more than one SOURCE):
    - Never merge facts from different sources into one undifferentiated list.
    - Group your answer per source. Start each source's section with the exact
      source name as a bold sub-heading on its own line — e.g. **Sannidhi_VK_Resume.pdf** —
      followed only by that source's bullet points.
    - Leave a blank line between one source's section and the next.
    - If the question only concerns one person/document (e.g. "which college did
      Sannidhi study in"), answer only for that source — do not mention the others.

    GROUNDING RULES:
    - Base your answer strictly on the provided context chunks.
    - If the context does not contain the answer to the specific question, state strictly: "I couldn't find a direct answer to your question in this document."
""")


def generate_rag_answer(
    question: str,
    context_chunks: list[dict] | list[str],
    chat_history: list[dict] | None = None,
) -> str:
    """
    Generate a grounded answer using Groq based on retrieved document chunks.

    This is the final step of the RAG pipeline:
      1. main.py embeds the question  → generate_query_embedding()
      2. main.py retrieves top chunks → db_service.search_similar_chunks*()
      3. main.py calls THIS function  → builds a prompt and asks the LLM

    context_chunks accepts either:
      - list[dict]: [{"text": "...", "source": "Sannidhi_VK_Resume.pdf"}, ...]
        (preferred for batch/multi-file chats — lets the model attribute facts
        to the correct document instead of blending them together)
      - list[str]: plain text chunks (backwards-compatible; all treated as
        coming from a single unnamed "Document" source)

    Args:
        question:       The user's raw question string.
        context_chunks: Retrieved chunks from the vector similarity search.
        chat_history:   Optional list of prior turns — each a dict with
                        {"role": "user"|"assistant", "content": "..."}.
                        Only the last 6 turns are injected to keep context
                        within the model's token budget.

    Returns:
        The LLM's answer as a markdown-formatted string.

    Raises:
        RuntimeError: If GROQ_API_KEY is missing, or if the API call fails.
    """
    if not _groq_client:
        raise RuntimeError("GROQ_API_KEY is missing from your .env file.")

    # Guard: no chunks means the vector search found nothing relevant.
    if not context_chunks:
        return "I couldn't find any relevant content in this document to answer your question."

    # ── Normalize input shape ────────────────────────────────────────────────
    # Unify both list[dict] and list[str] inputs into a flat list of
    # (source_label, text) pairs so the grouping logic below is uniform.
    normalized: list[tuple[str, str]] = []
    for chunk in context_chunks:
        if isinstance(chunk, dict):
            # dict form: carry the source filename for attribution.
            normalized.append((chunk.get("source") or "Document", chunk.get("text", "")))
        else:
            # plain string form: label everything as "Document".
            normalized.append(("Document", chunk))

    # ── Group chunks by source ───────────────────────────────────────────────
    # Keeping each document's chunks together (instead of interleaving) helps
    # the LLM attribute facts correctly in multi-document answers.
    # dict.setdefault preserves first-seen insertion order (Python 3.7+).
    grouped: dict[str, list[str]] = {}
    for source, text in normalized:
        grouped.setdefault(source, []).append(text)

    # Build the context block injected into the prompt.
    # Each source gets a clear === SOURCE: ... === header so the LLM sees
    # which document each chunk belongs to.
    context_block = "\n\n".join(
        f"=== SOURCE: {source} ===\n"
        + "\n---\n".join(f"[Chunk {i + 1}]: {text}" for i, text in enumerate(texts))
        for source, texts in grouped.items()
    )

    # ── Build the messages array ─────────────────────────────────────────────
    # Start with the system prompt that governs how the model should answer.
    messages = [{"role": "system", "content": _RAG_SYSTEM_PROMPT}]

    # Inject prior conversation turns for multi-turn context.
    # We limit to the last 6 turns (≈12 messages) to avoid token overflow.
    if chat_history:
        for msg in chat_history[-12:]:
            role = msg.get("role", "user")
            messages.append({"role": role, "content": msg["content"]})

    # The current user turn: context block + question fused into a single message.
    # The context is embedded here (not as a separate system message) so the
    # model treats it as the immediate grounding material for THIS question.
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
            # Low temperature keeps answers factual; higher values introduce
            # creative but potentially hallucinated responses.
            temperature=0.2,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        raise RuntimeError(f"Groq API Chat Error: {exc}") from exc
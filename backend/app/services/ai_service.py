"""
ai_service.py
-------------
Wraps all LLM interactions — all via Groq Cloud API:
  - Groq embeddings (`nomic-embed-text-v1_5`) for 768-dimensional vector embeddings.
  - Groq Cloud API (`llama-3.3-70b-versatile`) for fast AI Summaries and RAG Chat.

Note: This service no longer depends on a local Ollama instance. Embeddings are
generated via Groq's cloud embeddings endpoint, which is required for deployment
on Render (or any environment without a local Ollama daemon).
"""

import os
import textwrap

from groq import Groq
from dotenv import load_dotenv

load_dotenv()
load_dotenv("backend/.env")

# ── Groq Connection (For Embeddings, Summaries & RAG Chat) ─────────────────
_GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
_groq_client = Groq(api_key=_GROQ_API_KEY) if _GROQ_API_KEY else None
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile").strip()

# ── Embedding Model (Groq Cloud — replaces local Ollama) ───────────────────
EMBEDDING_MODEL = os.environ.get("GROQ_EMBEDDING_MODEL", "nomic-embed-text-v1_5")
EMBEDDING_DIMENSION = 768  # unchanged — no Supabase pgvector migration needed


# ── Embedding Functions (Groq Cloud) ────────────────────────────────────────
#
# nomic-embed-text expects a task-instruction prefix on the raw text:
#   "search_document: <text>"  when embedding chunks to be stored/retrieved
#   "search_query: <text>"     when embedding an incoming user question
# This distinction is preserved below, matching the original Ollama behaviour.

def _require_groq_client() -> Groq:
    if not _groq_client:
        raise RuntimeError("GROQ_API_KEY is missing from your .env file.")
    return _groq_client


def generate_embedding(text: str) -> list[float]:
    """Generate a 768-dim vector embedding for a single text chunk."""
    client = _require_groq_client()
    try:
        response = client.embeddings.create(
            input=f"search_document: {text}",
            model=EMBEDDING_MODEL,
        )
        return response.data[0].embedding
    except Exception as exc:
        raise RuntimeError(f"Groq embedding error: {exc}") from exc


def generate_embeddings_batch(chunks: list[str]) -> list[list[float]]:
    """Generate embeddings for a list of text chunks in a single batched call."""
    if not chunks:
        return []

    client = _require_groq_client()
    prefixed_chunks = [f"search_document: {chunk}" for chunk in chunks]

    try:
        response = client.embeddings.create(
            input=prefixed_chunks,
            model=EMBEDDING_MODEL,
        )
        return [item.embedding for item in response.data]
    except Exception as exc:
        raise RuntimeError(f"Groq batch embedding error: {exc}") from exc


def generate_query_embedding(query: str) -> list[float]:
    """Generate an embedding vector for a user search query."""
    client = _require_groq_client()
    try:
        response = client.embeddings.create(
            input=f"search_query: {query}",
            model=EMBEDDING_MODEL,
        )
        return response.data[0].embedding
    except Exception as exc:
        raise RuntimeError(f"Groq query embedding error: {exc}") from exc


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
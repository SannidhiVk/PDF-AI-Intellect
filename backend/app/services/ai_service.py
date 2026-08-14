"""
ai_service.py
-------------
Wraps all LLM interactions using LOCAL Ollama models — no cloud API key,
no billing, no quota limits.

Requires:
  1. Ollama installed and running locally: https://ollama.com/download
  2. Models pulled once via terminal:
         ollama pull nomic-embed-text
         ollama pull llama3.1
  3. `pip install ollama` (see requirements.txt)

Responsibilities:
  - Generating text embeddings via the local `nomic-embed-text` model
    (768 dimensions — matches the Supabase pgvector(768) column).
  - Generating structured PDF summaries via the local `llama3.1` model.
  - Answering user questions using a RAG (Retrieval-Augmented Generation)
    prompt constructed from retrieved document chunks.
"""

import os
import textwrap

import ollama
from dotenv import load_dotenv

load_dotenv()

# ── Ollama connection ──────────────────────────────────────────────────────
# Defaults to Ollama's standard local address. Override in .env only if you
# run Ollama on a different host/port (e.g. a remote machine or Docker).
_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
_client = ollama.Client(host=_OLLAMA_HOST)

# ── Model identifiers ─────────────────────────────────────────────────────────
EMBEDDING_MODEL = os.environ.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
CHAT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "llama3.2:3b")

# nomic-embed-text outputs 768-dim vectors — matches Supabase pgvector(768)
EMBEDDING_DIMENSION = 768


def _friendly_connection_error(exc: Exception) -> RuntimeError:
    """
    Ollama's raw connection errors are cryptic (e.g. 'Connection refused').
    Wrap them in a clear, actionable message.
    """
    return RuntimeError(
        "Could not reach Ollama. Make sure the Ollama app is running "
        f"(default: {_OLLAMA_HOST}) and that you've pulled the required "
        f"models: `ollama pull {EMBEDDING_MODEL}` and `ollama pull {CHAT_MODEL}`. "
        f"Original error: {exc}"
    )


# ── Embedding ─────────────────────────────────────────────────────────────────
# nomic-embed-text is an ASYMMETRIC embedding model: it expects different
# prefixes depending on whether you're embedding a document chunk (to be
# stored/searched) or a user query (to search WITH). Using the right prefix
# meaningfully improves retrieval accuracy — this mirrors what Gemini's
# task_type="retrieval_document" / "retrieval_query" did.

def generate_embedding(text: str) -> list[float]:
    """
    Generate a 768-dimensional embedding vector for a single text string,
    treated as a DOCUMENT chunk (used when storing PDF chunks).

    Args:
        text: The text to embed (a single document chunk).

    Returns:
        A list of 768 floats representing the semantic embedding.
    """
    try:
        response = _client.embeddings(
            model=EMBEDDING_MODEL,
            prompt=f"search_document: {text}",
        )
    except Exception as exc:
        raise _friendly_connection_error(exc) from exc

    return response["embedding"]


def generate_embeddings_batch(chunks: list[str]) -> list[list[float]]:
    """
    Generate embeddings for a list of text chunks sequentially.

    Args:
        chunks: List of text strings to embed.

    Returns:
        A list of embedding vectors, one per input chunk.
    """
    return [generate_embedding(chunk) for chunk in chunks]


def generate_query_embedding(query: str) -> list[float]:
    """
    Generate an embedding for a user query, treated as a QUERY (used when
    searching for relevant chunks during chat).

    Args:
        query: The user's question string.

    Returns:
        A 768-dimensional embedding vector.
    """
    try:
        response = _client.embeddings(
            model=EMBEDDING_MODEL,
            prompt=f"search_query: {query}",
        )
    except Exception as exc:
        raise _friendly_connection_error(exc) from exc

    return response["embedding"]


# ── Summarisation ─────────────────────────────────────────────────────────────

_SUMMARY_SYSTEM_PROMPT = textwrap.dedent("""\
    You are an expert document analyst producing a summary for someone who has
    NOT read the source document. Precision and grounding matter more than
    polish.

    GROUNDING RULES (do not violate these):
    - Use ONLY information that is explicitly present in the document text
      you are given. Never supplement with outside knowledge, assumptions,
      or general facts about the topic — even if you "know" more about it.
    - Do not infer facts, numbers, dates, or names that are not stated
      verbatim or near-verbatim in the text.
    - If the extracted text is fragmented, garbled, or clearly incomplete
      (e.g. from OCR/PDF extraction artifacts), summarise only what is
      legible and add one line under "Extraction Notes" flagging this —
      omit that section entirely if the text is clean.
    - If the document is too short or sparse to support a section below
      (e.g. no clear insights beyond the overview), write "Not enough
      content in the document to determine this" for that section instead
      of inventing filler bullets.

    LANGUAGE:
    - Respond in the same language as the source document. If the document
      mixes languages, use whichever language dominates the body text.

    STYLE:
    - Never open with meta-commentary such as "Based on the provided text"
      or "This document appears to be about". State facts directly.
    - Be concise: prefer one well-written sentence over three vague ones.
    - Scale the number of bullet points to the document's actual depth —
      a 2-page memo should not be padded to look like a 40-page report.

    Format your response EXACTLY as follows — do NOT add extra sections,
    headers, or commentary outside this structure:

    ## 📄 Document Summary

    **Overview:**
    <2–3 sentences: what this document is, who it's for/from, and its
    primary purpose>

    **Key Topics Covered:**
    • <topic 1 — one line, specific not generic>
    • <topic 2>
    • <topic 3>
    (as many as are genuinely present in the text; do not pad to a fixed count)

    **Main Insights & Takeaways:**
    • <a concrete conclusion, finding, decision, or recommendation from the text>
    • <insight 2>
    • <insight 3>
    (only include insights actually stated or clearly implied in the text)

    **Key Figures & Data:**
    • <notable number, date, name, or statistic and its context>
    (Only include this section header at all if the document actually
    contains a figure/date/statistic worth surfacing. If there is nothing
    like that, skip both the header and this section completely — do not
    write "none", "N/A", or any placeholder text in its place.)

    **Document Type:** <e.g., Research Paper / Report / Manual / Contract /
    Meeting Notes / Other — infer from structure and tone>
""")


def generate_summary(text: str) -> str:
    """
    Generate a structured, bullet-point summary of full PDF text.

    Truncates very long documents to the first 30 000 characters to stay
    within the local model's practical context window, and tells the model
    up front whether truncation happened so it doesn't imply completeness
    it can't back up.

    Args:
        text: The full extracted PDF text.

    Returns:
        A markdown-formatted summary string.
    """
    was_truncated = len(text) > 30_000
    truncated_text = text[:30_000] if was_truncated else text

    truncation_note = (
        "\n\nNOTE: This is only the first ~30,000 characters of a longer "
        "document. Summarise faithfully based on this excerpt alone — do "
        "not claim to cover the full document."
        if was_truncated
        else ""
    )

    try:
        response = _client.chat(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Please summarise the following document:\n\n"
                        f"{truncated_text}{truncation_note}"
                    ),
                },
            ],
        )
    except Exception as exc:
        raise _friendly_connection_error(exc) from exc

    return response["message"]["content"].strip()


# ── RAG Chat ──────────────────────────────────────────────────────────────────

_RAG_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a helpful, precise document assistant. You answer questions
    strictly based on the provided context extracted from the user's document.

    Rules:
    - Answer ONLY using information present in the context below.
    - If the answer is not in the context, clearly state:
      "I couldn't find a direct answer to your question in this document."
    - Keep answers concise but complete.
    - Use bullet points or numbered lists when listing multiple items.
    - Do NOT fabricate information or draw on external knowledge.
    - The context may be split into multiple chunks, and adjacent chunks may
      contain OVERLAPPING or REPEATED text at their boundaries — this is a
      normal artifact of how the document was split, not a signal that a
      section is "already covered." Read every chunk fully before answering.
    - When a question asks you to list, name, or enumerate items (e.g. "list
      all X", "name every Y"), you MUST scan ALL provided chunks for
      qualifying items before responding. Do not stop after finding items in
      just the first chunk that mentions the topic — the same topic may
      continue or repeat across other chunks too. Deduplicate identical
      items, but never omit an item because a similar-looking one appeared
      earlier in the context.
""")


def generate_rag_answer(
    question: str,
    context_chunks: list[str],
    chat_history: list[dict] | None = None,
) -> str:
    """
    Build a RAG prompt from retrieved chunks and get a grounded answer.

    Args:
        question:       The user's natural-language question.
        context_chunks: Ordered list of the most relevant text chunks
                        retrieved from the vector database.

    Returns:
        The model's grounded answer as a string.
    """
    if not context_chunks:
        return (
            "I couldn't find any relevant content in this document "
            "to answer your question."
        )

    # Assemble context block
    context_block = "\n\n---\n\n".join(
        f"[Chunk {i + 1}]:\n{chunk}"
        for i, chunk in enumerate(context_chunks)
    )

    # Build conversation history block (last 3 turns = 6 messages max)
    history_block = ""
    if chat_history:
        recent = chat_history[-6:]
        history_lines = []
        for msg in recent:
            role_label = "User" if msg["role"] == "user" else "Assistant"
            history_lines.append(f"{role_label}: {msg['content']}")
        history_block = "\n\nPREVIOUS CONVERSATION:\n" + "\n".join(history_lines)

    prompt = textwrap.dedent(f"""\
        CONTEXT FROM DOCUMENT:
        {context_block}{history_block}

        USER QUESTION:
        {question}

        Please answer the question based solely on the context above.
    """)

    try:
        response = _client.chat(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _RAG_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
    except Exception as exc:
        raise _friendly_connection_error(exc) from exc

    return response["message"]["content"].strip()
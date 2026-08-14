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
CHAT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "llama3.1")

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
    You are an expert document analyst. Your task is to produce a concise,
    structured summary of the provided document text.

    Format your response EXACTLY as follows — do NOT deviate:

    ## 📄 Document Summary

    **Overview:**
    <2–3 sentence high-level description of what the document is about>

    **Key Topics Covered:**
    • <topic 1>
    • <topic 2>
    • <topic 3>
    (include as many bullet points as are relevant, minimum 3)

    **Main Insights & Takeaways:**
    • <insight or conclusion 1>
    • <insight or conclusion 2>
    • <insight or conclusion 3>

    **Document Type:** <e.g., Research Paper / Report / Manual / Contract / Other>
""")


def generate_summary(text: str) -> str:
    """
    Generate a structured, bullet-point summary of full PDF text.

    Truncates very long documents to the first 30 000 characters to stay
    within the local model's practical context window.

    Args:
        text: The full extracted PDF text.

    Returns:
        A markdown-formatted summary string.
    """
    truncated_text = text[:30_000] if len(text) > 30_000 else text

    try:
        response = _client.chat(
            model=CHAT_MODEL,
            messages=[
                {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Please summarise the following document:\n\n{truncated_text}",
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
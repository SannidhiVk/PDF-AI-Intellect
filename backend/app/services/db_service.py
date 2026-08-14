"""
db_service.py
-------------
All Supabase database interactions:
  - Saving document metadata to the `documents` table.
  - Bulk-inserting chunk text + embedding vectors into `document_chunks`.
  - Performing cosine-similarity vector search via a Supabase RPC function
    (match_document_chunks) for retrieval-augmented generation.

Design note — lazy singleton client:
  The Supabase client is intentionally NOT created at module import time.
  Python executes `from app.services import db_service` before the calling
  module's `load_dotenv()` has run, which means os.environ would still be
  empty and create_client() would receive empty strings → "Invalid API key".

  The fix: `_get_client()` creates the client on the first actual DB call
  (i.e., at request time), by which point main.py's load_dotenv() has
  already populated os.environ with values from .env.
"""

import os
from typing import Any

from dotenv import load_dotenv
from supabase import create_client, Client

# Always call load_dotenv() here too so that db_service works correctly
# even when imported independently of main.py (e.g., in tests/scripts).
load_dotenv()

# ── Lazy singleton ─────────────────────────────────────────────────────────────
# The client is created once on the first call to _get_client() and then
# cached in this module-level variable for all subsequent calls.
_supabase_client: Client | None = None


def _get_client() -> Client:
    """
    Return the shared Supabase client, creating it on the first call.

    Raises:
        ValueError: If SUPABASE_URL or SUPABASE_KEY are missing or empty,
                    with a clear message pointing at the .env file.
    """
    global _supabase_client

    if _supabase_client is not None:
        return _supabase_client

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()

    missing: list[str] = []
    if not url:
        missing.append("SUPABASE_URL")
    if not key:
        missing.append("SUPABASE_KEY")

    if missing:
        raise ValueError(
            f"Missing or empty environment variable(s): {', '.join(missing)}. "
            "Make sure your .env file exists in the backend/ directory and "
            "contains valid values for SUPABASE_URL and SUPABASE_KEY. "
            "Copy backend/.env.example → backend/.env and fill in your keys."
        )

    _supabase_client = create_client(url, key)
    return _supabase_client


# ── Document Metadata ─────────────────────────────────────────────────────────

def save_document_metadata(
    user_id: str,
    file_name: str,
    file_url: str,
) -> dict[str, Any]:
    """
    Insert a new row into the `documents` table and return the saved record.

    Args:
        user_id:   The authenticated user's UUID (from Supabase Auth).
        file_name: Original filename of the uploaded PDF.
        file_url:  Public or signed URL of the file stored in Supabase Storage.

    Returns:
        The inserted row as a dict (includes auto-generated `id` and
        `created_at`).

    Raises:
        RuntimeError: If the Supabase insert fails.
    """
    payload = {
        "user_id": user_id,
        "file_name": file_name,
        "file_url": file_url,
    }

    response = _get_client().table("documents").insert(payload).execute()

    if not response.data:
        raise RuntimeError(
            f"Failed to save document metadata. Supabase response: {response}"
        )

    return response.data[0]


# ── Document Chunks ───────────────────────────────────────────────────────────

def store_document_chunks(
    document_id: str,
    chunks: list[str],
    embeddings: list[list[float]],
) -> None:
    """
    Bulk-insert text chunks and their embedding vectors into `document_chunks`.

    Args:
        document_id: UUID of the parent document row.
        chunks:      List of text strings (one per chunk).
        embeddings:  Parallel list of embedding vectors (same length as chunks).

    Raises:
        ValueError:   If chunks and embeddings lengths do not match.
        RuntimeError: If the Supabase insert fails.
    """
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"Chunks ({len(chunks)}) and embeddings ({len(embeddings)}) "
            "must have the same length."
        )

    rows = [
        {
            "document_id": document_id,
            "content": chunk,
            "embedding": embedding,
        }
        for chunk, embedding in zip(chunks, embeddings)
    ]

    # Supabase accepts bulk inserts in a single call
    response = _get_client().table("document_chunks").insert(rows).execute()

    if not response.data:
        raise RuntimeError(
            f"Failed to store document chunks. Supabase response: {response}"
        )


# ── Vector Similarity Search ──────────────────────────────────────────────────

def search_similar_chunks(
    document_id: str,
    query_embedding: list[float],
    match_count: int = 5,
) -> list[str]:
    """
    Retrieve the top-K most semantically similar chunks for a given query.

    Calls the `match_document_chunks` Postgres function (defined via RPC),
    which uses pgvector's `<=>` cosine distance operator under the hood.

    Args:
        document_id:     UUID of the document to search within.
        query_embedding: 768-dimensional embedding of the user's question.
        match_count:     How many top chunks to return (default: 5).

    Returns:
        A list of content strings for the top matching chunks, ordered by
        descending similarity.

    Raises:
        RuntimeError: If the RPC call fails.
    """
    response = _get_client().rpc(
        "match_document_chunks",
        {
            "query_embedding": query_embedding,
            "match_document_id": document_id,
            "match_count": match_count,
        },
    ).execute()

    if response.data is None:
        raise RuntimeError(
            f"Vector search RPC failed. Supabase response: {response}"
        )

    # Each row has a `content` field; return just the text strings
    return [row["content"] for row in response.data]


# ── Document Fetch ────────────────────────────────────────────────────────────

def get_document_by_id(document_id: str) -> dict[str, Any] | None:
    """
    Fetch a single document's metadata row by its UUID.

    Args:
        document_id: UUID of the document.

    Returns:
        The document row as a dict, or None if not found.
    """
    response = (
        _get_client().table("documents")
        .select("*")
        .eq("id", document_id)
        .single()
        .execute()
    )
    return response.data

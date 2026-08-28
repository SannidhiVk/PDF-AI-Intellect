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
    summary: str | None = None,
    word_count: int | None = None,
) -> dict[str, Any]:
    """
    Insert a new row into the `documents` table and return the saved record.

    Args:
        user_id:    The authenticated user's UUID (from Supabase Auth).
        file_name:  Original filename of the uploaded PDF.
        file_url:   Public or signed URL of the file stored in Supabase Storage.
        summary:    The Groq-generated AI summary text, if already computed
                    at upload time. Persisted so it survives page reloads —
                    previously this was only returned in the upload response
                    and never written to the DB, which is why summaries
                    disappeared after the initial request.
        word_count: Optional word count of the summary, so the frontend
                    doesn't need to recompute it on every render.

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
    if summary is not None:
        payload["summary"] = summary
    if word_count is not None:
        payload["word_count"] = word_count

    # Use the service-role client so the insert succeeds regardless of the
    # documents RLS INSERT policy (which checks auth.uid(), a value that is
    # always NULL when the request originates from this server-side Python
    # client rather than from a browser session).  Ownership is enforced
    # here by including user_id in the payload, not by relying on RLS alone.
    response = _get_service_client().table("documents").insert(payload).execute()

    if not response.data:
        raise RuntimeError(
            f"Failed to save document metadata. Supabase response: {response}"
        )

    return response.data[0]


def update_document_summary(
    document_id: str,
    summary: str,
    word_count: int | None = None,
) -> dict[str, Any]:
    """
    Persist an AI-generated summary onto an existing document row.

    Use this when summary generation happens as a separate step AFTER the
    initial upload insert (e.g. triggered by an "Analyze" button, or a
    background job) rather than inline during save_document_metadata().

    This is likely the fix needed if your flow is:
      1. Upload PDF -> insert row via save_document_metadata() (no summary yet)
      2. User clicks "Analyze" -> Groq generates summary
      3. <-- summary was being returned to the frontend but never written
             back to Supabase here, so it vanished on next page load.

    Args:
        document_id: UUID of the document row to update.
        summary:     The generated summary text.
        word_count:  Optional word count to cache alongside it.

    Returns:
        The updated row as a dict.

    Raises:
        RuntimeError: If the update fails or matches no row.
    """
    payload: dict[str, Any] = {"summary": summary}
    if word_count is not None:
        payload["word_count"] = word_count

    response = (
        _get_service_client()
        .table("documents")
        .update(payload)
        .eq("id", document_id)
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            f"Failed to update summary for document {document_id}. "
            f"Response: {response}"
        )
    return response.data[0]


# ── Document Chunks ───────────────────────────────────────────────────────────

def store_document_chunks(
    document_id: str,
    chunks: list[str],
    embeddings: list[list[float]],
    metadatas: list[dict] | None = None,
) -> None:
    """
    Bulk-insert text chunks and their embedding vectors into `document_chunks`.

    Args:
        document_id: UUID of the parent document row.
        chunks:      List of text strings (one per chunk).
        embeddings:  Parallel list of embedding vectors (same length as chunks).
        metadatas:   Optional parallel list of metadata dicts (section_index,
                     split_method, source filename etc.) to persist in the
                     `metadata` JSONB column. Defaults to empty dicts if not
                     provided. Previously this parameter was not accepted here
                     even though main.py was passing it, causing a TypeError
                     on every upload → chunks were silently never stored →
                     retrieval always returned empty results.

    Raises:
        ValueError:   If chunks and embeddings lengths do not match.
        RuntimeError: If the Supabase insert fails.
    """
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"Chunks ({len(chunks)}) and embeddings ({len(embeddings)}) "
            "must have the same length."
        )

    # Normalise metadatas: if not provided, use empty dicts for each chunk.
    _metadatas = metadatas if metadatas and len(metadatas) == len(chunks) else [{} for _ in chunks]

    rows = [
        {
            "document_id": document_id,
            "content": chunk,
            "embedding": embedding,
            "metadata": meta,
        }
        for chunk, embedding, meta in zip(chunks, embeddings, _metadatas)
    ]

    # Use the service-role client for the same reason as save_document_metadata:
    # the RLS INSERT policy on document_chunks checks auth.uid(), which is
    # always NULL for server-side Python requests → inserts would silently
    # fail or raise 403 without the service key.
    response = _get_service_client().table("document_chunks").insert(rows).execute()

    if not response.data:
        raise RuntimeError(
            f"Failed to store document chunks. Supabase response: {response}"
        )


# ── Vector Similarity Search ──────────────────────────────────────────────────

def search_similar_chunks(
    document_id: str,
    query_embedding: list[float],
    match_count: int = 5,
    match_threshold: float = 0.5,
) -> list[str]:
    """
    Retrieve the top-K most semantically similar chunks for a given query.

    Calls the `match_document_chunks` Postgres function (defined via RPC),
    which uses pgvector's `<=>` cosine distance operator under the hood.

    IMPORTANT: The parameter names in the RPC payload below MUST match EXACTLY
    what the deployed SQL function declares — PostgREST resolves RPC calls via
    strict named-parameter matching against its schema cache, not fuzzy or
    positional matching. The live deployed function (supabase_schema.sql)
    declares the document filter parameter as `filter_document_id`.

    Args:
        document_id:     UUID of the document to search within.
        query_embedding: 768-dimensional embedding of the user's question.
        match_count:     How many top chunks to return (default: 5).
        match_threshold: Minimum cosine similarity (0-1) for a chunk to be
                          considered a match (default: 0.5). Raise this for
                          stricter relevance, lower it if legitimate chunks
                          are being filtered out. Applied here in Python,
                          since the underlying SQL function does not accept
                          a threshold parameter.

    Returns:
        A list of content strings for the top matching chunks, ordered by
        descending similarity.

    Raises:
        RuntimeError: If the RPC call fails (response.data is None).

    NOTE — parameter name history:
      The SQL function's document-filter parameter is `filter_document_id`
      (as declared in supabase_schema.sql). A previous edit incorrectly
      changed this to `match_document_id` in the Python RPC payload, causing
      PostgREST to fail to resolve the function (unrecognised parameter name),
      returning None for response.data and raising a RuntimeError → 500.
      Reverted to `filter_document_id` to match the live deployed signature.
      The `match_threshold` parameter never existed in the SQL function; it is
      applied as a Python-side filter on the returned `similarity` column.
    """
    response = _get_service_client().rpc(
        "match_document_chunks",
        {
            "query_embedding": query_embedding,
            "filter_document_id": document_id,
            "match_count": match_count,
        },
    ).execute()

    if response.data is None:
        raise RuntimeError(
            f"Vector search RPC failed. Supabase response: {response}"
        )

    # Apply the similarity threshold here, since the SQL function returns
    # all top `match_count` rows regardless of similarity score.
    filtered_rows = [
        row for row in response.data
        if row.get("similarity", 0) >= match_threshold
    ]

    return [row["content"] for row in filtered_rows]


def search_similar_chunks_multi(
    document_ids: list[str],
    query_embedding: list[float],
    match_count: int = 8,
    match_threshold: float = 0.0,
) -> list[str]:
    """
    Retrieve the top-K most semantically similar chunks across MULTIPLE documents.

    Calls the `match_document_chunks_multi` Postgres RPC which uses
    `WHERE document_id = ANY(filter_document_ids)` to search all provided
    documents in a single query, ordered by cosine similarity globally.

    Args:
        document_ids:    List of document UUIDs to search across.
        query_embedding: 768-dimensional embedding of the user's question.
        match_count:     How many top chunks to return globally (default: 8).
        match_threshold: Minimum cosine similarity (0-1) to include a chunk.

    Returns:
        A list of content strings for the top matching chunks, ordered by
        descending similarity, sourced from any of the provided documents.

    Raises:
        ValueError:   If document_ids is empty.
        RuntimeError: If the RPC call fails.
    """
    if not document_ids:
        raise ValueError("document_ids must contain at least one ID.")

    response = _get_service_client().rpc(
        "match_document_chunks_multi",
        {
            "query_embedding": query_embedding,
            "filter_document_ids": document_ids,
            "match_count": match_count,
        },
    ).execute()

    if response.data is None:
        raise RuntimeError(
            f"Multi-doc vector search RPC failed. Supabase response: {response}"
        )

    filtered_rows = [
        row for row in response.data
        if row.get("similarity", 0) >= match_threshold
    ]

    return [row["content"] for row in filtered_rows]



# ── Document Fetch ────────────────────────────────────────────────────────────

def get_document_by_id(document_id: str, user_id: str | None = None) -> dict[str, Any] | None:
    """
    Fetch a single document's metadata row by its UUID.

    When user_id is provided the query also filters by that column, acting as
    an ownership check: a document that exists but belongs to a different user
    will return None (same as not found), preventing information leakage.

    Args:
        document_id: UUID of the document.
        user_id:     Optional – the requesting user's UUID.  When supplied, the
                     query adds a WHERE user_id = ? filter.

    Returns:
        The document row as a dict, or None if not found / not owned.
    """
    query = (
        _get_service_client().table("documents")
        .select("*")
        .eq("id", document_id)
    )
    if user_id:
        query = query.eq("user_id", user_id)

    response = query.maybe_single().execute()
    return response.data


def get_documents_by_user(user_id: str) -> list[dict[str, Any]]:
    """
    Fetch all document metadata rows that belong to the given user.

    Results are ordered newest-first so the sidebar can render them as a
    chronological history without any client-side sorting.

    Args:
        user_id: The authenticated user's UUID.

    Returns:
        A list of document row dicts (may be empty if the user has no docs).
    """
    response = (
        _get_service_client().table("documents")
        .select("id, file_name, file_url, created_at, summary, word_count")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return response.data or []


def delete_document(document_id: str, user_id: str) -> bool:
    """
    Delete a document and all of its dependent rows (chunks, in that order
    to satisfy the FK constraint), scoped to the requesting user.

    Ownership is enforced here in Python (same pattern as the rest of this
    file) rather than relying on RLS, since all writes go through the
    service-role client.

    Args:
        document_id: UUID of the document to delete.
        user_id:     The authenticated user's UUID — the delete only
                     succeeds if this user owns the document.

    Returns:
        True if a document row was deleted, False if no matching document
        was found for this user (already gone, wrong ID, or not theirs).
    """
    client = _get_service_client()

    # 1. Verify ownership before deleting anything
    owned = (
        client.table("documents")
        .select("id")
        .eq("id", document_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not owned.data:
        return False

    # 2. Delete dependent chunks first to avoid FK constraint violations
    client.table("document_chunks").delete().eq("document_id", document_id).execute()

    # 3. Delete dependent shares and comments too, so nothing orphans
    client.table("document_shares").delete().eq("document_id", document_id).execute()
    client.table("document_comments").delete().eq("document_id", document_id).execute()

    # 4. Finally delete the document row itself
    response = (
        client.table("documents")
        .delete()
        .eq("id", document_id)
        .eq("user_id", user_id)
        .execute()
    )
    return bool(response.data)


# ── Service-Role Client (bypasses RLS) ───────────────────────────────────────
# Used ONLY for server-side operations that legitimately need to act outside
# of Row Level Security, e.g. inserting guest comments where user_id IS NULL
# (no authenticated user context).  Never expose this key to the frontend.

_service_client: Client | None = None


def _get_service_client() -> Client:
    """
    Return a Supabase client initialised with the service-role key.
    Falls back to the anon client if SUPABASE_SERVICE_KEY is not set or invalid,
    ensuring local development and public RLS policies continue to work smoothly.
    """
    global _service_client
    if _service_client is not None:
        return _service_client

    url = os.environ.get("SUPABASE_URL", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

    # If key is empty or a placeholder string, fall back immediately
    if not service_key or service_key.startswith("your-") or not service_key.startswith("ey"):
        return _get_client()

    try:
        _service_client = create_client(url, service_key)
        return _service_client
    except Exception as exc:
        import warnings
        warnings.warn(
            f"Failed to initialise service-role client ({exc}). Falling back to anon client.",
            RuntimeWarning,
            stacklevel=2,
        )
        return _get_client()


# ── Share Management ──────────────────────────────────────────────────────────

def create_share(document_id: str, user_id: str) -> dict[str, Any]:
    """
    Create (or re-activate) a share link for a document.

    If an inactive share already exists for this document it is re-activated
    rather than creating a new row, keeping token URLs stable.

    Returns:
        The document_shares row as a dict.

    IMPORTANT — required SQL migration:
      The `created_by` column must exist in the live `document_shares` table.
      If you see a 500 with 'column document_shares.created_by does not exist',
      run this in the Supabase SQL Editor before using the sharing feature:

          ALTER TABLE document_shares
              ADD COLUMN IF NOT EXISTS created_by UUID
              REFERENCES auth.users(id) ON DELETE CASCADE;

          UPDATE document_shares SET created_by = (
              SELECT user_id FROM documents
              WHERE documents.id = document_shares.document_id
          ) WHERE created_by IS NULL;

          ALTER TABLE document_shares ALTER COLUMN created_by SET NOT NULL;

    Note: uses the service-role client — the anon client never forwards the
    user's JWT to PostgREST, so auth.uid() is NULL for every call made through
    it — the RLS policy "auth.uid() = created_by" would then reject every
    insert/update unconditionally. Ownership is instead enforced here in Python
    via the .eq("created_by", user_id) filter.
    """
    client = _get_service_client()

    # Check for existing row (active or inactive) owned by this user.
    # Each document has at most one share row per owner, so filtering by
    # both document_id and created_by uniquely identifies it.
    existing = (
        client.table("document_shares")
        .select("*")
        .eq("document_id", document_id)
        .eq("created_by", user_id)
        .limit(1)
        .execute()
    )

    if existing.data:
        row = existing.data[0]
        if not row["is_active"]:
            # Re-activate the existing share rather than creating a duplicate
            updated = (
                client.table("document_shares")
                .update({"is_active": True})
                .eq("id", row["id"])
                .execute()
            )
            return updated.data[0]
        return row

    # No existing row — create a fresh share link for this document + owner
    response = (
        client.table("document_shares")
        .insert({"document_id": document_id, "created_by": user_id})
        .execute()
    )
    if not response.data:
        raise RuntimeError(
            f"Failed to create share link for document {document_id}. "
            f"Supabase response: {response}. "
            "If you see '42703 column document_shares.created_by does not exist', "
            "run the SQL migration shown in the create_share() docstring above."
        )
    return response.data[0]


def revoke_share(document_id: str, user_id: str) -> bool:
    """
    Deactivate the share link for a document (sets is_active = FALSE).

    Returns True if a row was updated, False if no active share existed.

    Filters by both document_id AND created_by so only the owner can
    revoke their own share. Uses the service-role client to bypass RLS
    (same reason as create_share — auth.uid() is always NULL for
    server-side Python requests).
    """
    response = (
        _get_service_client().table("document_shares")
        .update({"is_active": False})
        .eq("document_id", document_id)
        .eq("created_by", user_id)
        .eq("is_active", True)
        .execute()
    )
    return bool(response.data)


def get_share_by_token(token: str) -> dict[str, Any] | None:
    """
    Fetch an *active* share row by its token UUID.
    Uses the service-role client so it bypasses RLS.

    Returns None if the token is not found, malformed, or the share is
    inactive. Malformed tokens (e.g. the frontend accidentally sending
    the literal string "undefined") are rejected here BEFORE hitting
    Postgres — otherwise PostgREST raises a raw 22P02 error ("invalid
    input syntax for type uuid") which bubbles up as an unhandled 500
    instead of a clean "not found".
    """
    import uuid as _uuid

    try:
        _uuid.UUID(str(token))
    except (ValueError, AttributeError, TypeError):
        return None

    response = (
        _get_service_client().table("document_shares")
        .select("*, documents(id, file_name, summary)")
        .eq("share_token", token)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return response.data[0]


# ── Comment Management ────────────────────────────────────────────────────────

def get_comments(document_id: str) -> list[dict[str, Any]]:
    """
    Fetch all comments for a document, ordered oldest-first.
    Returns a flat list; the caller is responsible for threading.
    Uses the service-role client so it works for both owner and public share.
    """
    response = (
        _get_service_client().table("document_comments")
        .select("*")
        .eq("document_id", document_id)
        .order("created_at", desc=False)
        .execute()
    )
    return response.data or []


def create_comment(
    document_id: str,
    content: str,
    author_name: str,
    user_id: str | None = None,
    parent_id: str | None = None,
) -> dict[str, Any]:
    """
    Insert a new comment (or reply) into document_comments.

    Always uses the service-role client so the insert succeeds regardless of
    the document_comments RLS INSERT policy (which checks auth.uid(), a value
    that is always NULL when the request originates from this server-side
    Python client rather than from a browser session).

    Ownership and access control are enforced here in Python:
      - Authenticated owner: user_id is set in the payload.
      - Guests via share link: user_id is omitted (NULL in DB).
      - parent_id is validated as a UUID before being sent to Postgres to
        avoid raw 22P02 "invalid input syntax for type uuid" errors.

    This mirrors the same pattern used by create_share / revoke_share.

    Args:
        document_id:  UUID of the parent document.
        content:      Comment text.
        author_name:  Display name (required for guests; set from profile for auth users).
        user_id:      Supabase auth UID, or None for guests.
        parent_id:    UUID of the parent comment for replies, or None.

    Returns:
        The inserted row as a dict.
    """
    import uuid as _uuid

    payload: dict[str, Any] = {
        "document_id": document_id,
        "content": content,
        "author_name": author_name,
    }
    if user_id:
        payload["user_id"] = user_id

    # Validate parent_id as a real UUID before sending to Postgres.
    # Passing the Python string "None" or a malformed value would cause a
    # raw 22P02 Postgres error that surfaces as an opaque 500.
    if parent_id is not None:
        try:
            _uuid.UUID(str(parent_id))
            payload["parent_id"] = parent_id
        except (ValueError, AttributeError):
            # Treat invalid/garbage parent_id as "no parent"
            pass

    # Always use the service-role client for server-side comment inserts.
    # The anon client does NOT forward the user's JWT to PostgREST, so
    # auth.uid() is NULL inside Postgres — the RLS INSERT policy
    # (auth.uid() = user_id) then rejects the write unconditionally,
    # even when user_id is a valid authenticated UUID.
    # This is the exact same root cause that was fixed for create_share /
    # revoke_share. Using the service-role client bypasses RLS; ownership is
    # enforced above by Python logic (user_id payload field).
    client = _get_service_client()

    response = client.table("document_comments").insert(payload).execute()
    if not response.data:
        raise RuntimeError(f"Failed to insert comment. Response: {response}")
    return response.data[0]


def delete_comment(comment_id: str, user_id: str) -> bool:
    """
    Delete a comment owned by user_id (cascades to replies via FK).

    Returns True if deleted, False if not found / not authorised.
    """
    response = (
        _get_client().table("document_comments")
        .delete()
        .eq("id", comment_id)
        .eq("user_id", user_id)
        .execute()
    )
    return bool(response.data)


def owner_delete_comment(comment_id: str, owner_user_id: str) -> bool:
    """
    Allow the document owner to delete any comment on their document.
    Verifies ownership via a JOIN to the documents table.
    Uses the service-role client because the owner RLS DELETE policy
    requires auth.uid() = documents.user_id — valid for REST requests
    but not directly enforceable in this server-side context.

    Returns True if deleted, False if the comment wasn't found or the
    caller doesn't own the parent document.
    """
    # First verify the caller owns the parent document
    verify = (
        _get_service_client().table("document_comments")
        .select("id, documents!document_comments_document_id_fkey(user_id)")
        .eq("id", comment_id)
        .limit(1)
        .execute()
    )
    if not verify.data:
        return False

    doc_owner = verify.data[0].get("documents", {}).get("user_id")
    if doc_owner != owner_user_id:
        return False

    response = (
        _get_service_client().table("document_comments")
        .delete()
        .eq("id", comment_id)
        .execute()
    )
    return bool(response.data)


# ── Shared Chat History ────────────────────────────────────────────────────────

def get_share_chat_history(share_token: str) -> list[dict[str, Any]]:
    """
    Fetch all persisted chat messages for the given share token, ordered
    chronologically (oldest first).

    Args:
        share_token: The UUID string used in the share URL (/share/<token>).

    Returns:
        A list of dicts, each with keys: id, role, content, created_at.
        Returns an empty list if no messages exist yet.

    Raises:
        RuntimeError: If the Supabase query fails.
    """
    try:
        response = (
            _get_service_client()
            .table("share_chat_messages")
            .select("id, role, content, created_at")
            .eq("share_token", share_token)
            .order("created_at", desc=False)
            .execute()
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch share chat history: {exc}") from exc

    return response.data or []


def save_share_chat_messages(
    share_token: str,
    user_content: str,
    assistant_content: str,
) -> None:
    """
    Persist a user message and the corresponding AI reply for the given share
    token. Both are inserted in a single batch call so they appear atomically.

    Args:
        share_token:       The share link token (UUID string).
        user_content:      The raw question text submitted by the visitor.
        assistant_content: The AI-generated answer text.

    Raises:
        RuntimeError: If the Supabase insert fails.
    """
    rows = [
        {"share_token": share_token, "role": "user",      "content": user_content},
        {"share_token": share_token, "role": "assistant",  "content": assistant_content},
    ]
    try:
        _get_service_client().table("share_chat_messages").insert(rows).execute()
    except Exception as exc:
        raise RuntimeError(f"Failed to save share chat messages: {exc}") from exc
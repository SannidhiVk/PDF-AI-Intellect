"""
main.py
-------
FastAPI application entry point.

Defines three core endpoints:
  POST /api/process-pdf   – Upload PDF → extract → chunk → embed → store
  POST /api/summarize     – Summarise a document by ID or raw text
  POST /api/chat          – RAG-based Q&A against a stored document

Run locally with:
    uvicorn app.main:app --reload --port 8000

Critical loading order
----------------------
load_dotenv() MUST be called before importing any service module.
Python executes each imported module's top-level code at import time.
If load_dotenv() comes after the imports, os.environ is still empty when
the services try to read config — causing connection/config errors.
"""

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

# ══ MUST be first: populate os.environ from .env BEFORE importing services ══
from dotenv import load_dotenv
load_dotenv()  # noqa: E402 — intentionally before service imports

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from typing import Literal, Optional  # noqa: E402

from app.auth import get_current_user  # noqa: E402
from app.services import ai_service, db_service, pdf_service  # noqa: E402

# ── Lifespan (startup / shutdown hooks) ───────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: validate config and log key status. Shutdown: log teardown."""

    # ── Configuration check ──────────────────────────────────────────────────
    # NOTE: Gemini has been replaced with local Ollama models (see
    # app/services/ai_service.py). Ollama needs no API key — it just needs
    # to be running locally — so it's no longer part of this required check.
    required_vars = {
        "SUPABASE_URL": os.environ.get("SUPABASE_URL", "").strip(),
        "SUPABASE_KEY": os.environ.get("SUPABASE_KEY", "").strip(),
    }

    missing = [name for name, val in required_vars.items() if not val]
    if missing:
        # Crash early with a clear message rather than a cryptic API error
        raise RuntimeError(
            f"❌  Missing environment variable(s): {', '.join(missing)}. "
            "Ensure backend/.env exists and contains all required keys. "
            "See backend/.env.example for the required format."
        )

    # Print masked values so you can visually confirm the right keys are loaded
    def _mask(val: str) -> str:
        return val[:6] + "*" * (len(val) - 6) if len(val) > 6 else "***"

    print("🚀  PDF AI Assistant backend is starting up…")
    print("✅  Configuration check: Supabase keys loaded successfully")
    print(f"     SUPABASE_URL   → {required_vars['SUPABASE_URL']}")
    print(f"     SUPABASE_KEY   → {_mask(required_vars['SUPABASE_KEY'])}")

    # ── Ollama connectivity check (warning only — non-fatal) ─────────────────
    # We don't crash the server if Ollama isn't reachable yet, since it's a
    # separate local app the developer starts independently and may not be
    # running the moment uvicorn boots. Endpoints that need it will raise a
    # clear error (via ai_service._friendly_connection_error) when called.
    try:
        import httpx
        ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        with httpx.Client(timeout=2.0) as http_client:
            http_client.get(f"{ollama_host}/api/tags")
        print(f"✅  Ollama reachable at {ollama_host}")
    except Exception:
        ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        print(
            f"⚠️   Ollama not reachable at {ollama_host}. "
            "Start the Ollama app before uploading/summarizing/chatting, "
            "or PDF processing calls will fail."
        )

    yield

    print("🛑  PDF AI Assistant backend is shutting down…")


# ── App Initialisation ─────────────────────────────────────────────────────────

app = FastAPI(
    title="PDF AI Assistant API",
    description=(
        "A production-ready FastAPI backend for uploading PDFs, "
        "generating AI summaries, and chatting with document content "
        "using locally-hosted Ollama models (llama3.1 + nomic-embed-text) "
        "and Supabase pgvector."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ───────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],   # Next.js dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response Schemas ────────────────────────────────────────────────

class ProcessPdfResponse(BaseModel):
    document_id: str
    file_name: str
    chunk_count: int
    summary: str
    message: str


class SummarizeRequest(BaseModel):
    document_id: str | None = None
    text: str | None = None


class SummarizeResponse(BaseModel):
    document_id: str | None
    summary: str


class DocumentListItem(BaseModel):
    id: str
    file_name: str
    file_url: str
    created_at: str


class ChatHistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    document_id: str
    question: str
    chat_history: list[ChatHistoryItem] = []


class ChatResponse(BaseModel):
    document_id: str
    question: str
    answer: str
    sources_used: int


# ── Health Check ───────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
async def root():
    """Simple health-check endpoint."""
    return {"status": "ok", "message": "PDF AI Assistant API is running."}


# ── Endpoint 0: List Documents ───────────────────────────────────────────────

@app.get(
    "/api/documents",
    response_model=list[DocumentListItem],
    summary="List all documents uploaded by the authenticated user",
    tags=["Documents"],
)
async def list_documents(user_id: str = Depends(get_current_user)):
    """
    Return all documents that belong to the requesting user, newest-first.

    This endpoint is what the frontend calls on page load to rebuild the
    sidebar's "Recent Documents" history.  Without it, the list lived only
    in React state and was wiped on every hard-refresh.

    Security: user_id comes from the verified JWT — the client cannot
    spoof it.  db_service.get_documents_by_user applies a WHERE user_id = ?
    filter on the service-role client, so RLS is not needed here (but it
    is still enabled as a defence-in-depth measure for direct DB access).
    """
    docs = db_service.get_documents_by_user(user_id)
    return [
        DocumentListItem(
            id=str(d["id"]),
            file_name=d["file_name"],
            file_url=d.get("file_url") or "",
            created_at=str(d["created_at"]),
        )
        for d in docs
    ]


# ── Endpoint 1: Process PDF ───────────────────────────────────────────────────

@app.post(
    "/api/process-pdf",
    response_model=ProcessPdfResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload & process a PDF",
    tags=["Documents"],
)
async def process_pdf(
    file: UploadFile = File(..., description="The PDF file to process."),
    user_id: str = Depends(get_current_user),
):
    """
    Full pipeline for a newly uploaded PDF:

    1. Read raw bytes from the upload.
    2. Extract text with PyMuPDF.
    3. Split into overlapping chunks.
    4. Generate local Ollama embeddings for each chunk.
    5. Save document metadata + chunks to Supabase.
    6. Return the new document_id for use in subsequent calls.
    """
    # ── Validate file type ────────────────────────────────────────────────────
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        # content_type can be unreliable; also check filename extension
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only PDF files are accepted.",
            )

    # ── Read file bytes ───────────────────────────────────────────────────────
    try:
        file_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read uploaded file: {exc}",
        )

    # ── Extract text ──────────────────────────────────────────────────────────
    try:
        raw_text = pdf_service.extract_text_from_pdf(file_bytes)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # ── Chunk text ────────────────────────────────────────────────────────────
    try:
        chunks = pdf_service.split_text_into_chunks(raw_text)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # ── Generate embeddings ───────────────────────────────────────────────────
    try:
        embeddings = ai_service.generate_embeddings_batch(chunks)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ollama embedding generation failed: {exc}",
        )

    # ── Generate summary ──────────────────────────────────────────────────────
    try:
        summary = ai_service.generate_summary(raw_text)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ollama summarisation failed: {exc}",
        )

    # ── Save to Supabase ──────────────────────────────────────────────────────
    try:
        # For now, file_url is a placeholder — wire Supabase Storage upload
        # in Phase 3 when the frontend passes the stored URL.
        doc_record = db_service.save_document_metadata(
            user_id=user_id,
            file_name=file.filename or "unknown.pdf",
            file_url="",   # Updated once Storage upload is integrated
        )
        document_id = doc_record["id"]

        db_service.store_document_chunks(
            document_id=document_id,
            chunks=chunks,
            embeddings=embeddings,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database operation failed: {exc}",
        )

    return ProcessPdfResponse(
        document_id=document_id,
        file_name=file.filename or "unknown.pdf",
        chunk_count=len(chunks),
        summary=summary,
        message="PDF processed and stored successfully.",
    )


# ── Endpoint 2: Summarise ─────────────────────────────────────────────────────

@app.post(
    "/api/summarize",
    response_model=SummarizeResponse,
    summary="Generate a structured PDF summary",
    tags=["AI"],
)
async def summarize(body: SummarizeRequest, user_id: str = Depends(get_current_user)):
    """
    Generate a structured, bullet-point summary of a PDF document.

    Accepts EITHER:
    - `document_id`: fetches the document text from Supabase (future: store
       raw text or re-extract from Storage). For now, requires `text` as well.
    - `text`: raw extracted text passed directly by the client.

    At least one of the two fields must be provided.
    """
    if not body.document_id and not body.text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either 'document_id' or 'text' (or both).",
        )

    # Use raw text if supplied, else resolve from document_id (extend later)
    text_to_summarise = body.text

    if not text_to_summarise and body.document_id:
        # Future: retrieve stored raw text from Supabase Storage or a
        # `documents.raw_text` column. For now, raise a descriptive error.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "When providing only 'document_id', please also include "
                "'text' (the extracted PDF text). Direct text storage "
                "will be added in Phase 3."
            ),
        )

    try:
        summary = ai_service.generate_summary(text_to_summarise)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ollama summarisation failed: {exc}",
        )

    return SummarizeResponse(
        document_id=body.document_id,
        summary=summary,
    )


# ── Endpoint 3: Chat (RAG) ────────────────────────────────────────────────────

@app.post(
    "/api/chat",
    response_model=ChatResponse,
    summary="Ask a question about a stored document (RAG)",
    tags=["AI"],
)
async def chat(body: ChatRequest, user_id: str = Depends(get_current_user)):
    """
    Answer a natural-language question using Retrieval-Augmented Generation:

    1. Verify the requesting user owns the document (prevents cross-user reads).
    2. Embed the user's question with local Ollama embeddings (query-style prefix).
    3. Search the vector DB for the top-5 most similar chunks.
    4. Build a grounded RAG prompt and get an answer from the local llama3.2:3b model.
    5. Return the answer along with how many source chunks were used.
    """
    if not body.question.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Question must not be empty.",
        )

    # ── Ownership check ─────────────────────────────────────────────────────────
    # Without this check, any authenticated user who knows (or guesses) a
    # document_id can read another user's document content via the RAG API.
    doc = db_service.get_document_by_id(body.document_id, user_id=user_id)
    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found or you do not have permission to access it.",
        )

    # ── Embed the query ───────────────────────────────────────────────────────
    try:
        query_embedding = ai_service.generate_query_embedding(body.question)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to generate query embedding: {exc}",
        )

    # ── Retrieve relevant chunks ──────────────────────────────────────────────
    # match_threshold=0.0 is deliberately generous (see note below on
    # similarity filtering). match_count was reduced from 10 -> 5:
    # with CHUNK_SIZE=2000 chars in pdf_service.py, 10 chunks could reach
    # ~20,000 chars (~5,000 tokens) once combined with the RAG system prompt
    # and chat history — enough to exceed CHAT_NUM_CTX (4096 by default) and
    # trigger Ollama's "failed to allocate CPU buffer" error. 5 chunks
    # (~10,000 chars / ~2,500 tokens) leaves safe headroom. Raise this again
    # only alongside a matching increase to OLLAMA_NUM_CTX in .env, and only
    # if you've confirmed you have the RAM for it.
    #
    # For small documents (a handful of chunks), similarity filtering can
    # incorrectly drop chunks that are still relevant just because their
    # section mixes topics (diluting the embedding). Retrieving more chunks
    # and letting the LLM's grounded prompt do the relevance filtering is
    # safer than dropping context at the DB layer. Revisit these numbers
    # once you're testing with larger, multi-page documents.
    RAG_MATCH_COUNT = 5
    try:
        context_chunks = db_service.search_similar_chunks(
            document_id=body.document_id,
            query_embedding=query_embedding,
            match_count=RAG_MATCH_COUNT,
            match_threshold=0.0,
        )
        print(f"\n{'='*60}\nRETRIEVED {len(context_chunks)} CHUNKS:")
        for i, chunk in enumerate(context_chunks, 1):
            print(f"\n--- Chunk {i} ({len(chunk)} chars) ---\n{chunk}")
        print(f"{'='*60}\n")
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Vector search failed: {exc}",
        )

    # ── Generate grounded answer ──────────────────────────────────────────────
    try:
        answer = ai_service.generate_rag_answer(
            question=body.question,
            context_chunks=context_chunks,
            chat_history=[h.model_dump() for h in body.chat_history],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ollama answer generation failed: {exc}",
        )

    return ChatResponse(
        document_id=body.document_id,
        question=body.question,
        answer=answer,
        sources_used=len(context_chunks),
    )


# ════════════════════════════════════════════════════════════════════════════
#  SHARE & COMMENTS FEATURE
# ════════════════════════════════════════════════════════════════════════════

# ── Additional Pydantic Schemas ───────────────────────────────────────────────

class ShareCreateResponse(BaseModel):
    share_token: str
    share_url: str
    is_active: bool


class ShareInfoResponse(BaseModel):
    document_id: str
    file_name: str


class CommentCreate(BaseModel):
    content: str
    author_name: str = "Anonymous"
    parent_id: Optional[str] = None  # None = top-level; UUID = reply


class CommentResponse(BaseModel):
    id: str
    parent_id: Optional[str]
    author_name: str
    content: str
    created_at: str
    is_own: bool
    replies: list["CommentResponse"] = []


CommentResponse.model_rebuild()


class ShareChatRequest(BaseModel):
    question: str
    chat_history: list[ChatHistoryItem] = []


# ── Helper: validate share token ──────────────────────────────────────────────

def _get_active_share(token: str) -> dict:
    """
    Look up an active share row by token.
    Raises HTTP 404 if the token is invalid or the link has been revoked.
    """
    share = db_service.get_share_by_token(token)
    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This share link is no longer active or does not exist.",
        )
    return share


def _build_share_url(token: str) -> str:
    frontend = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")
    return f"{frontend}/share/{token}"


def _thread_comments(
    flat: list[dict],
    requesting_user_id: Optional[str] = None,
) -> list[CommentResponse]:
    """
    Convert a flat list of comment rows (ordered by created_at asc) into a
    threaded structure where each top-level comment has a .replies list.
    """
    index: dict[str, CommentResponse] = {}
    roots: list[CommentResponse] = []

    for row in flat:
        cr = CommentResponse(
            id=row["id"],
            parent_id=row.get("parent_id"),
            author_name=row["author_name"],
            content=row["content"],
            created_at=row["created_at"],
            is_own=(
                requesting_user_id is not None
                and row.get("user_id") == requesting_user_id
            ),
        )
        index[row["id"]] = cr

    for row in flat:
        cr = index[row["id"]]
        pid = row.get("parent_id")
        if pid and pid in index:
            index[pid].replies.append(cr)
        else:
            roots.append(cr)

    return roots


# ── Endpoint 4: Create / get share link (owner only) ─────────────────────────

@app.post(
    "/api/documents/{document_id}/share",
    response_model=ShareCreateResponse,
    status_code=status.HTTP_200_OK,
    summary="Create or re-activate a shareable link for a document",
    tags=["Sharing"],
)
async def create_share(
    document_id: str,
    user_id: str = Depends(get_current_user),
):
    """
    Creates a unique shareable link for the given document.
    If a link already exists (even if inactive) the same token is returned
    and the link is re-activated, keeping the URL stable.

    Only the document owner can create a share link.
    """
    try:
        share = db_service.create_share(document_id=document_id, user_id=user_id)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    return ShareCreateResponse(
        share_token=str(share["share_token"]),
        share_url=_build_share_url(share["share_token"]),
        is_active=share["is_active"],
    )


# ── Endpoint 5: Revoke share link (owner only) ────────────────────────────────

@app.delete(
    "/api/documents/{document_id}/share",
    status_code=status.HTTP_200_OK,
    summary="Revoke (deactivate) the shareable link for a document",
    tags=["Sharing"],
)
async def revoke_share(
    document_id: str,
    user_id: str = Depends(get_current_user),
):
    """
    Sets the share link's is_active flag to FALSE.  Anyone who navigates to
    the old /share/<token> URL will now receive a 404.
    """
    revoked = db_service.revoke_share(document_id=document_id, user_id=user_id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active share link found for this document.",
        )
    return {"message": "Share link revoked successfully."}


# ── Endpoint 6: Public share info (no auth) ───────────────────────────────────

@app.get(
    "/api/share/{token}",
    response_model=ShareInfoResponse,
    summary="Get document info for a shared link (public)",
    tags=["Sharing"],
)
async def get_share_info(token: str):
    """
    Validate a share token and return basic document information (filename).
    No authentication required — the token is the access control.
    """
    share = _get_active_share(token)
    doc = share.get("documents") or {}
    return ShareInfoResponse(
        document_id=share["document_id"],
        file_name=doc.get("file_name", "Document"),
    )


# ── Endpoint 7: Public share chat (no auth) ───────────────────────────────────

@app.post(
    "/api/share/{token}/chat",
    response_model=ChatResponse,
    summary="RAG chat via share link (public)",
    tags=["Sharing"],
)
async def share_chat(token: str, body: ShareChatRequest):
    """
    Full RAG chat experience for share-link recipients.
    Validates the token, then runs the identical pipeline as /api/chat.
    No JWT required — the share token is the access gate.
    """
    share = _get_active_share(token)
    document_id = share["document_id"]

    if not body.question.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Question must not be empty.",
        )

    try:
        query_embedding = ai_service.generate_query_embedding(body.question)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to generate query embedding: {exc}",
        )

    RAG_MATCH_COUNT = 5
    try:
        context_chunks = db_service.search_similar_chunks(
            document_id=document_id,
            query_embedding=query_embedding,
            match_count=RAG_MATCH_COUNT,
            match_threshold=0.0,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Vector search failed: {exc}",
        )

    try:
        answer = ai_service.generate_rag_answer(
            question=body.question,
            context_chunks=context_chunks,
            chat_history=[h.model_dump() for h in body.chat_history],
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ollama answer generation failed: {exc}",
        )

    return ChatResponse(
        document_id=document_id,
        question=body.question,
        answer=answer,
        sources_used=len(context_chunks),
    )


# ── Endpoint 8: List comments via share token (public) ───────────────────────

@app.get(
    "/api/share/{token}/comments",
    response_model=list[CommentResponse],
    summary="List comments for a shared document (public)",
    tags=["Comments"],
)
async def list_shared_comments(token: str):
    """
    Return all comments (threaded) for the document behind this share token.
    No authentication required.
    """
    share = _get_active_share(token)
    flat = db_service.get_comments(share["document_id"])
    return _thread_comments(flat, requesting_user_id=None)


# ── Endpoint 9: Post comment via share token (public / guest) ─────────────────

@app.post(
    "/api/share/{token}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Post a comment on a shared document (guests welcome)",
    tags=["Comments"],
)
async def post_shared_comment(token: str, body: CommentCreate):
    """
    Post a comment (or reply) on a shared document.
    No account needed — guests just supply an author_name.
    The share token acts as access control: only people with the link can comment.
    """
    share = _get_active_share(token)

    if not body.content.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Comment content must not be empty.",
        )
    if not body.author_name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please enter a display name.",
        )

    try:
        row = db_service.create_comment(
            document_id=share["document_id"],
            content=body.content.strip(),
            author_name=body.author_name.strip(),
            user_id=None,   # guest — no account
            parent_id=body.parent_id,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    return CommentResponse(
        id=row["id"],
        parent_id=row.get("parent_id"),
        author_name=row["author_name"],
        content=row["content"],
        created_at=row["created_at"],
        is_own=False,
    )


# ── Endpoint 9b: Post comment directly as owner (no share link required) ─────

@app.post(
    "/api/documents/{document_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Post a comment as the document owner (dashboard, no share link needed)",
    tags=["Comments"],
)
async def post_owner_comment(
    document_id: str,
    body: CommentCreate,
    user_id: str = Depends(get_current_user),
):
    """
    Lets the document owner comment from the dashboard WITHOUT requiring a
    share link to already exist. Previously all comment writes were forced
    through /api/share/{token}/comments, which meant the owner literally
    could not comment until they'd clicked "Share" at least once — that
    was a bug, not a design choice. This endpoint uses the owner's JWT
    directly instead.
    """
    if not body.content.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Comment content must not be empty.",
        )

    try:
        row = db_service.create_comment(
            document_id=document_id,
            content=body.content.strip(),
            author_name=(body.author_name.strip() or "You"),
            user_id=user_id,
            parent_id=body.parent_id,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    return CommentResponse(
        id=row["id"],
        parent_id=row.get("parent_id"),
        author_name=row["author_name"],
        content=row["content"],
        created_at=row["created_at"],
        is_own=True,
    )


# ── Endpoint 10: List comments (owner, from dashboard) ───────────────────────

@app.get(
    "/api/documents/{document_id}/comments",
    response_model=list[CommentResponse],
    summary="List all comments on a document (owner only)",
    tags=["Comments"],
)
async def list_document_comments(
    document_id: str,
    user_id: str = Depends(get_current_user),
):
    """
    Returns all threaded comments for a document.
    Only the document owner can access this endpoint.
    """
    flat = db_service.get_comments(document_id)
    return _thread_comments(flat, requesting_user_id=user_id)


# ── Endpoint 11: Delete a comment (owner of comment or doc owner) ─────────────

@app.delete(
    "/api/comments/{comment_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a comment (and its replies)",
    tags=["Comments"],
)
async def delete_comment(
    comment_id: str,
    user_id: str = Depends(get_current_user),
):
    """
    Delete a comment.  Either the comment's own author or the document owner
    can delete it.  Deleting a parent comment cascades to all its replies.
    """
    # Try as comment author first
    deleted = db_service.delete_comment(comment_id=comment_id, user_id=user_id)
    if not deleted:
        # Try as document owner
        deleted = db_service.owner_delete_comment(
            comment_id=comment_id, owner_user_id=user_id
        )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found or you are not authorised to delete it.",
        )
    return {"message": "Comment deleted successfully."}
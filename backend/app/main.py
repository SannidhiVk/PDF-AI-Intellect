"""
main.py
-------
FastAPI application entry point.

Defines core endpoints:
  POST /api/process-pdf   – Upload PDF → extract → chunk → embed → store
  POST /api/summarize     – Summarise a document by ID or raw text
  POST /api/chat          – RAG-based Q&A against a stored document

Run locally with:
    uvicorn app.main:app --reload --port 8000
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
    required_vars = {
        "SUPABASE_URL": os.environ.get("SUPABASE_URL", "").strip(),
        "SUPABASE_KEY": os.environ.get("SUPABASE_KEY", "").strip(),
        "GROQ_API_KEY": os.environ.get("GROQ_API_KEY", "").strip(),
        "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY", "").strip(),
    }

    missing = [name for name, val in required_vars.items() if not val]
    if missing:
        raise RuntimeError(
            f"❌ Missing environment variable(s): {', '.join(missing)}. "
            "Ensure backend/.env exists and contains all required keys."
        )

    def _mask(val: str) -> str:
        return val[:6] + "*" * (len(val) - 6) if len(val) > 6 else "***"

    print("🚀 PDF AI Assistant backend is starting up…")
    print("✅ Configuration check: Required keys loaded successfully")
    print(f"     SUPABASE_URL   → {required_vars['SUPABASE_URL']}")
    print(f"     SUPABASE_KEY   → {_mask(required_vars['SUPABASE_KEY'])}")
    print(f"     GROQ_API_KEY   → {_mask(required_vars['GROQ_API_KEY'])}")
    print(f"     GEMINI_API_KEY → {_mask(required_vars['GEMINI_API_KEY'])}")

    print("✅ Embeddings: using Gemini API (gemini-embedding-001, 768 dims)")

    yield

    print("🛑 PDF AI Assistant backend is shutting down…")


# ── App Initialisation ─────────────────────────────────────────────────────────

app = FastAPI(
    title="PDF AI Assistant API",
    description=(
        "A FastAPI backend for uploading PDFs, generating AI summaries, "
        "and chatting with document content using Groq Cloud API "
        "(llama-3.3-70b-versatile for chat/summaries) and Gemini API "
        "(gemini-embedding-001 for 768-dim embeddings) with Supabase pgvector."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ───────────────────────────────────────────────────────────────────────

_frontend_url = os.environ.get("FRONTEND_URL", "").strip().rstrip("/")
_allowed_origins = list(filter(None, [
    "http://localhost:3000",          # always allow local dev
    "http://127.0.0.1:3000",          # alternate local dev address
    _frontend_url if _frontend_url else None,  # production Vercel URL
]))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
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
    batch_id: str | None = None


class BatchDocumentItem(BaseModel):
    document_id: str
    file_name: str
    chunk_count: int
    summary: str
    word_count: int | None = None


class BatchFailedItem(BaseModel):
    filename: str
    error: str


class ProcessBatchResponse(BaseModel):
    batch_id: str
    title: str
    created_at: str
    documents: list[BatchDocumentItem]
    failed: list[BatchFailedItem] = []
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
    summary: str | None = None
    word_count: int | None = None
    batch_id: str | None = None


class BatchListItem(BaseModel):
    id: str
    title: str
    created_at: str
    documents: list[DocumentListItem] = []


class ChatHistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    document_id: str | None = None
    batch_id: str | None = None
    question: str
    chat_history: list[ChatHistoryItem] = []


class ChatResponse(BaseModel):
    document_id: str | None = None
    batch_id: str | None = None
    question: str
    answer: str
    sources_used: int


# ── Health Check ───────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
async def root():
    """Simple health-check endpoint."""
    return {"status": "ok", "message": "PDF AI Assistant API is running."}


# ── Endpoint 0: List Batches / Documents ──────────────────────────────────────

@app.get(
    "/api/batches",
    response_model=list[BatchListItem],
    summary="List all upload batches and their documents for the user",
    tags=["Batches"],
)
async def list_batches(user_id: str = Depends(get_current_user)):
    raw_batches = db_service.get_batches_by_user(user_id)
    items: list[BatchListItem] = []
    for b in raw_batches:
        raw_docs = b.get("documents") or []
        doc_items = [
            DocumentListItem(
                id=str(d["id"]),
                file_name=d["file_name"],
                file_url=d.get("file_url") or "",
                created_at=str(d["created_at"]),
                summary=d.get("summary"),
                word_count=d.get("word_count"),
                batch_id=str(b["id"]),
            )
            for d in raw_docs
        ]
        items.append(
            BatchListItem(
                id=str(b["id"]),
                title=b["title"],
                created_at=str(b["created_at"]),
                documents=doc_items,
            )
        )
    return items


@app.delete(
    "/api/batches/{batch_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete an upload batch (and all nested documents, chunks, comments)",
    tags=["Batches"],
)
async def delete_batch(
    batch_id: str,
    user_id: str = Depends(get_current_user),
):
    deleted = db_service.delete_batch(batch_id=batch_id, user_id=user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch not found or you do not have permission to delete it.",
        )
    return {"message": "Batch deleted successfully.", "batch_id": batch_id}


@app.get(
    "/api/documents",
    response_model=list[DocumentListItem],
    summary="List all documents uploaded by the authenticated user",
    tags=["Documents"],
)
async def list_documents(user_id: str = Depends(get_current_user)):
    docs = db_service.get_documents_by_user(user_id)
    return [
        DocumentListItem(
            id=str(d["id"]),
            file_name=d["file_name"],
            file_url=d.get("file_url") or "",
            created_at=str(d["created_at"]),
            summary=d.get("summary"),
            word_count=d.get("word_count"),
            batch_id=str(d.get("batch_id")) if d.get("batch_id") else None,
        )
        for d in docs
    ]


@app.delete(
    "/api/documents/{document_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a document (and its chunks, shares, comments)",
    tags=["Documents"],
)
async def delete_document(
    document_id: str,
    user_id: str = Depends(get_current_user),
):
    deleted = db_service.delete_document(document_id=document_id, user_id=user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found or you do not have permission to delete it.",
        )
    return {"message": "Document deleted successfully.", "document_id": document_id}


# ── Helper for processing a single PDF in a batch ─────────────────────────────

async def _process_pdf_file_contents(
    file_bytes: bytes,
    filename: str,
    user_id: str,
    batch_id: str | None = None,
) -> tuple[dict, int, str]:
    """
    Extracts text, chunks, embeds with Gemini, summarizes with Groq, and stores DB rows.
    Returns (doc_record, chunk_count, summary).
    """
    raw_text = pdf_service.extract_text_from_pdf(file_bytes)
    chunk_objects = pdf_service.split_text_into_chunks(raw_text, source_filename=filename)
    chunk_texts = [c.text for c in chunk_objects]
    chunk_metadatas = [c.metadata for c in chunk_objects]

    embeddings = ai_service.generate_embeddings_batch(chunk_texts)
    summary = ai_service.generate_summary(raw_text)

    doc_record = db_service.save_document_metadata(
        user_id=user_id,
        file_name=filename,
        file_url="",
        summary=summary,
        word_count=len(summary.split()) if summary else None,
        batch_id=batch_id,
    )
    document_id = doc_record["id"]

    db_service.store_document_chunks(
        document_id=document_id,
        chunks=chunk_texts,
        embeddings=embeddings,
        metadatas=chunk_metadatas,
        batch_id=batch_id,
    )

    return doc_record, len(chunk_objects), summary


# ── Endpoint 1a: Process Batch (Multiple PDFs in 1 Session) ───────────────────

@app.post(
    "/api/process-batch",
    response_model=ProcessBatchResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload & process multiple PDFs under one batch session",
    tags=["Batches"],
)
async def process_batch(
    files: list[UploadFile] = File(..., description="The PDF files to process together."),
    user_id: str = Depends(get_current_user),
):
    if not files:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No files provided.",
        )

    # Compute descriptive title
    if len(files) == 1:
        batch_title = files[0].filename or "Document"
    else:
        batch_title = f"Batch ({len(files)} files): {', '.join([f.filename or 'doc' for f in files[:2]])}"
        if len(files) > 2:
            batch_title += "..."

    batch_row = db_service.create_batch(user_id=user_id, title=batch_title)
    batch_id = str(batch_row["id"])

    succeeded_docs: list[BatchDocumentItem] = []
    failed_items: list[BatchFailedItem] = []

    # Fail-open loop: process each file independently
    for file in files:
        filename = file.filename or "unknown.pdf"
        try:
            if file.content_type not in ("application/pdf", "application/octet-stream") and not filename.lower().endswith(".pdf"):
                raise ValueError("Only PDF files are accepted.")

            file_bytes = await file.read()
            doc_rec, chunk_count, summary = await _process_pdf_file_contents(
                file_bytes=file_bytes,
                filename=filename,
                user_id=user_id,
                batch_id=batch_id,
            )
            succeeded_docs.append(
                BatchDocumentItem(
                    document_id=str(doc_rec["id"]),
                    file_name=filename,
                    chunk_count=chunk_count,
                    summary=summary,
                    word_count=doc_rec.get("word_count"),
                )
            )
        except Exception as exc:
            failed_items.append(BatchFailedItem(filename=filename, error=str(exc)))

    # If ALL files in the batch failed, clean up the empty batch shell and return 422
    if not succeeded_docs:
        db_service.delete_batch(batch_id=batch_id, user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"All {len(files)} file(s) failed to process: {'; '.join([f'{it.filename}: {it.error}' for it in failed_items])}",
        )

    return ProcessBatchResponse(
        batch_id=batch_id,
        title=batch_title,
        created_at=str(batch_row.get("created_at") or datetime.now(timezone.utc).isoformat()),
        documents=succeeded_docs,
        failed=failed_items,
        message=f"Processed {len(succeeded_docs)} of {len(files)} document(s) successfully.",
    )


# ── Endpoint 1b: Process Single PDF (Backward Compatible) ─────────────────────

@app.post(
    "/api/process-pdf",
    response_model=ProcessPdfResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload & process a single PDF",
    tags=["Documents"],
)
async def process_pdf(
    file: UploadFile = File(..., description="The PDF file to process."),
    user_id: str = Depends(get_current_user),
):
    filename = file.filename or "unknown.pdf"
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only PDF files are accepted.",
            )

    try:
        file_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read uploaded file: {exc}",
        )

    # Automatically create a 1-file batch for single upload
    try:
        batch_row = db_service.create_batch(user_id=user_id, title=filename)
        batch_id = str(batch_row["id"])
    except Exception:
        batch_id = None

    try:
        doc_record, chunk_count, summary = await _process_pdf_file_contents(
            file_bytes=file_bytes,
            filename=filename,
            user_id=user_id,
            batch_id=batch_id,
        )
    except ValueError as exc:
        if batch_id:
            db_service.delete_batch(batch_id=batch_id, user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except Exception as exc:
        if batch_id:
            db_service.delete_batch(batch_id=batch_id, user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Processing failed: {exc}",
        )

    return ProcessPdfResponse(
        document_id=str(doc_record["id"]),
        file_name=filename,
        chunk_count=chunk_count,
        summary=summary,
        batch_id=batch_id,
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
    if not body.document_id and not body.text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either 'document_id' or 'text' (or both).",
        )

    text_to_summarise = body.text

    if not text_to_summarise and body.document_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="When providing only 'document_id', please also include 'text'.",
        )

    try:
        summary = ai_service.generate_summary(text_to_summarise)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Groq summarisation failed: {exc}",
        )

    return SummarizeResponse(
        document_id=body.document_id,
        summary=summary,
    )


# ── Endpoint 3: Chat (RAG) ────────────────────────────────────────────────────

@app.post(
    "/api/chat",
    response_model=ChatResponse,
    summary="Ask a question about a stored batch or document (RAG)",
    tags=["AI"],
)
async def chat(body: ChatRequest, user_id: str = Depends(get_current_user)):
    if not body.question.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Question must not be empty.",
        )

    if not body.batch_id and not body.document_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Must provide either 'batch_id' or 'document_id'.",
        )

    # Verify ownership
    if body.batch_id:
        batch = db_service.get_batch_by_id(body.batch_id, user_id=user_id)
        if not batch:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Batch not found or you do not have permission to access it.",
            )
    elif body.document_id:
        doc = db_service.get_document_by_id(body.document_id, user_id=user_id)
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found or you do not have permission to access it.",
            )

    try:
        query_embedding = ai_service.generate_query_embedding(body.question)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to generate query embedding: {exc}",
        )

    RAG_MATCH_COUNT = 8 if body.batch_id else 5
    try:
        if body.batch_id:
            context_chunks = db_service.search_similar_chunks_by_batch(
                batch_id=body.batch_id,
                query_embedding=query_embedding,
                match_count=RAG_MATCH_COUNT,
                match_threshold=0.0,
            )
        else:
            context_chunks = db_service.search_similar_chunks(
                document_id=body.document_id,
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
            detail=f"Groq answer generation failed: {exc}",
        )

    return ChatResponse(
        document_id=body.document_id,
        batch_id=body.batch_id,
        question=body.question,
        answer=answer,
        sources_used=len(context_chunks),
    )


# ── Endpoint 3b: Multi-Document Chat (RAG across multiple docs) ───────────────

class MultiChatRequest(BaseModel):
    document_ids: list[str]
    question: str
    chat_history: list[ChatHistoryItem] = []


class MultiChatResponse(BaseModel):
    document_ids: list[str]
    question: str
    answer: str
    sources_used: int


@app.post(
    "/api/chat/multi",
    response_model=MultiChatResponse,
    summary="Ask a question across multiple documents (RAG)",
    tags=["AI"],
)
async def chat_multi(body: MultiChatRequest, user_id: str = Depends(get_current_user)):
    """
    RAG-based Q&A that retrieves context from multiple documents at once.
    All document IDs must belong to the authenticated user.
    """
    if len(body.document_ids) < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one document_id.",
        )
    if not body.question.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Question must not be empty.",
        )

    # Verify ownership of every document ID
    for doc_id in body.document_ids:
        doc = db_service.get_document_by_id(doc_id, user_id=user_id)
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document {doc_id} not found or you do not have permission to access it.",
            )

    try:
        query_embedding = ai_service.generate_query_embedding(body.question)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to generate query embedding: {exc}",
        )

    # If only one doc, fall back to the single-doc RPC for efficiency
    try:
        if len(body.document_ids) == 1:
            context_chunks = db_service.search_similar_chunks(
                document_id=body.document_ids[0],
                query_embedding=query_embedding,
                match_count=8,
                match_threshold=0.0,
            )
        else:
            context_chunks = db_service.search_similar_chunks_multi(
                document_ids=body.document_ids,
                query_embedding=query_embedding,
                match_count=8,
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
            detail=f"Groq answer generation failed: {exc}",
        )

    return MultiChatResponse(
        document_ids=body.document_ids,
        question=body.question,
        answer=answer,
        sources_used=len(context_chunks),
    )


# ════════════════════════════════════════════════════════════════════════════
#  SHARE & COMMENTS FEATURE
# ════════════════════════════════════════════════════════════════════════════

class ShareCreateResponse(BaseModel):
    share_token: str
    share_url: str
    is_active: bool


class ShareInfoResponse(BaseModel):
    document_id: str
    file_name: str
    summary: str | None = None


class CommentCreate(BaseModel):
    content: str
    author_name: str = "Anonymous"
    parent_id: Optional[str] = None


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


class ShareChatHistoryMessage(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: str


@app.get(
    "/api/share/{token}/chat-history",
    response_model=list[ShareChatHistoryMessage],
    summary="Get persisted chat history for a share link (public)",
    tags=["Sharing"],
)
async def get_share_chat_history(token: str):
    """
    Returns all saved chat messages for the given share token, ordered
    oldest-first. Used by the frontend to restore conversation history
    when the page is (re)loaded.
    """
    _get_active_share(token)  # 404 if token invalid / revoked

    try:
        rows = db_service.get_share_chat_history(token)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )

    return [
        ShareChatHistoryMessage(
            id=row["id"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
        )
        for row in rows
    ]



def _get_active_share(token: str) -> dict:
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
    revoked = db_service.revoke_share(document_id=document_id, user_id=user_id)
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active share link found for this document.",
        )
    return {"message": "Share link revoked successfully."}


@app.get(
    "/api/share/{token}",
    response_model=ShareInfoResponse,
    summary="Get document info for a shared link (public)",
    tags=["Sharing"],
)
async def get_share_info(token: str):
    share = _get_active_share(token)
    doc = share.get("documents") or {}
    return ShareInfoResponse(
        document_id=share["document_id"],
        file_name=doc.get("file_name", "Document"),
        summary=doc.get("summary"),
    )


@app.post(
    "/api/share/{token}/chat",
    response_model=ChatResponse,
    summary="RAG chat via share link (public)",
    tags=["Sharing"],
)
async def share_chat(token: str, body: ShareChatRequest):
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
            detail=f"Groq answer generation failed: {exc}",
        )

    # Persist the Q&A pair so future visitors see the full conversation history.
    # Non-fatal — a DB failure here should not break the chat response.
    try:
        db_service.save_share_chat_messages(
            share_token=token,
            user_content=body.question,
            assistant_content=answer,
        )
    except RuntimeError as exc:
        # Log but don't surface to the caller — the answer was generated fine.
        print(f"[share_chat] Warning: could not persist chat message: {exc}")

    return ChatResponse(
        document_id=document_id,
        question=body.question,
        answer=answer,
        sources_used=len(context_chunks),
    )


@app.get(
    "/api/share/{token}/comments",
    response_model=list[CommentResponse],
    summary="List comments for a shared document (public)",
    tags=["Comments"],
)
async def list_shared_comments(token: str):
    share = _get_active_share(token)
    flat = db_service.get_comments(share["document_id"])
    return _thread_comments(flat, requesting_user_id=None)


@app.post(
    "/api/share/{token}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Post a comment on a shared document (guests welcome)",
    tags=["Comments"],
)
async def post_shared_comment(token: str, body: CommentCreate):
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
            user_id=None,
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


@app.post(
    "/api/documents/{document_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Post a comment as the document owner",
    tags=["Comments"],
)
async def post_owner_comment(
    document_id: str,
    body: CommentCreate,
    user_id: str = Depends(get_current_user),
):
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
    flat = db_service.get_comments(document_id)
    return _thread_comments(flat, requesting_user_id=user_id)


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
    deleted = db_service.delete_comment(comment_id=comment_id, user_id=user_id)
    if not deleted:
        deleted = db_service.owner_delete_comment(
            comment_id=comment_id, owner_user_id=user_id
        )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Comment not found or you are not authorised to delete it.",
        )
    return {"message": "Comment deleted successfully."}


# ════════════════════════════════════════════════════════════════════════════
#  SHARE INVITE VIA EMAIL (BREVO API)
# ════════════════════════════════════════════════════════════════════════════

class ShareInviteRequest(BaseModel):
    recipient_email: str
    sender_name: str = "Someone"


@app.post(
    "/api/documents/{document_id}/share/invite",
    status_code=status.HTTP_200_OK,
    summary="Send a share-link invitation email to a recipient via Brevo",
    tags=["Sharing"],
)
async def send_share_invite(
    document_id: str,
    body: ShareInviteRequest,
    user_id: str = Depends(get_current_user),
):
    """
    Sends a formatted email to `recipient_email` containing the document's
    active share link using Brevo's transactional API. Requires BREVO_API_KEY in .env.
    """
    import httpx

    brevo_api_key = os.environ.get("BREVO_API_KEY", "").strip()
    sender_email = os.environ.get("BREVO_SENDER_EMAIL", "no-reply@pdfintellect.com").strip()
    sender_name = os.environ.get("BREVO_SENDER_NAME", "PDF Intellect").strip()

    if not brevo_api_key:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Email sending is not configured. "
                "Add BREVO_API_KEY to your backend/.env file."
            ),
        )

    # Get or create the share link
    try:
        share = db_service.create_share(document_id=document_id, user_id=user_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    share_url = _build_share_url(share["share_token"])

    # Fetch doc name for the email
    doc = db_service.get_document_by_id(document_id, user_id=user_id)
    file_name = doc.get("file_name", "a document") if doc else "a document"

    html_body = f"""
    <div style="font-family:Inter,sans-serif;background:#0a0a0f;padding:40px 0;min-height:100vh">
      <div style="max-width:520px;margin:0 auto;background:#111117;border:1px solid #2a2a3a;border-radius:20px;overflow:hidden">
        <!-- Header -->
        <div style="background:linear-gradient(135deg,#6d28d9,#4f46e5);padding:36px 40px;text-align:center">
          <div style="font-size:24px;font-weight:700;color:#fff;letter-spacing:-0.5px">📄 PDF Intellect</div>
          <div style="font-size:13px;color:rgba(255,255,255,0.7);margin-top:4px">AI Document Assistant</div>
        </div>
        <!-- Body -->
        <div style="padding:36px 40px">
          <p style="color:#e5e7eb;font-size:15px;line-height:1.6;margin:0 0 20px">
            Hi there,<br><br>
            <strong style="color:#fff">{body.sender_name}</strong> has shared a document with you on
            <strong style="color:#fff">PDF Intellect</strong>:
          </p>
          <!-- Doc card -->
          <div style="background:#1a1a2e;border:1px solid #2a2a3a;border-radius:14px;padding:20px 24px;margin-bottom:28px">
            <div style="display:flex;align-items:center;gap:12px">
              <div style="background:linear-gradient(135deg,#6d28d9,#4f46e5);border-radius:10px;width:40px;height:40px;display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0">📄</div>
              <div>
                <div style="color:#f3f4f6;font-weight:600;font-size:14px">{file_name}</div>
                <div style="color:#6b7280;font-size:12px;margin-top:2px">Shared with you · No account needed</div>
              </div>
            </div>
          </div>
          <!-- CTA -->
          <div style="text-align:center;margin-bottom:28px">
            <a href="{share_url}"
               style="display:inline-block;background:linear-gradient(135deg,#6d28d9,#4f46e5);color:#fff;text-decoration:none;font-weight:600;font-size:14px;border-radius:12px;padding:14px 32px;box-shadow:0 4px 20px rgba(109,40,217,0.4)">
              View Document →
            </a>
          </div>
          <p style="color:#6b7280;font-size:12px;text-align:center;margin:0">
            You can view the summary, ask the AI questions, and leave comments.<br>
            No account or sign-up required.
          </p>
        </div>
        <!-- Footer -->
        <div style="border-top:1px solid #2a2a3a;padding:20px 40px;text-align:center">
          <p style="color:#4b5563;font-size:11px;margin:0">
            This invitation was sent via PDF Intellect. If you weren't expecting this, you can safely ignore it.
          </p>
        </div>
      </div>
    </div>
    """

    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": body.recipient_email}],
        "subject": f"{body.sender_name} shared \"{file_name}\" with you",
        "htmlContent": html_body,
    }

    headers = {
        "accept": "application/json",
        "api-key": brevo_api_key,
        "content-type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                json=payload,
                headers=headers,
            )
            if res.status_code not in (200, 201, 202):
                res_data = res.json() if res.headers.get("content-type") == "application/json" else {}
                err_msg = res_data.get("message") or res.text
                raise Exception(f"Brevo API Error ({res.status_code}): {err_msg}")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to send email via Brevo: {exc}",
        )

    return {
        "message": f"Invitation sent to {body.recipient_email}.",
        "share_url": share_url,
    }
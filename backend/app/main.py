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
    docs = db_service.get_documents_by_user(user_id)
    return [
        DocumentListItem(
            id=str(d["id"]),
            file_name=d["file_name"],
            file_url=d.get("file_url") or "",
            created_at=str(d["created_at"]),
            summary=d.get("summary"),
            word_count=d.get("word_count"),
        )
        for d in docs
    ]


# ── Endpoint 0b: Delete Document ─────────────────────────────────────────────

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
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        if not (file.filename or "").lower().endswith(".pdf"):
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

    try:
        raw_text = pdf_service.extract_text_from_pdf(file_bytes)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    try:
        chunks = pdf_service.split_text_into_chunks(raw_text)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # Gemini embedding generation
    try:
        embeddings = ai_service.generate_embeddings_batch(chunks)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gemini embedding generation failed: {exc}",
        )

    # Groq API summary generation
    try:
        summary = ai_service.generate_summary(raw_text)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Groq summarisation failed: {exc}",
        )

    try:
        doc_record = db_service.save_document_metadata(
            user_id=user_id,
            file_name=file.filename or "unknown.pdf",
            file_url="",
            summary=summary,
            word_count=len(summary.split()) if summary else None,
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
    summary="Ask a question about a stored document (RAG)",
    tags=["AI"],
)
async def chat(body: ChatRequest, user_id: str = Depends(get_current_user)):
    if not body.question.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Question must not be empty.",
        )

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

    RAG_MATCH_COUNT = 5
    try:
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
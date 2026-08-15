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

# ══ MUST be first: populate os.environ from .env BEFORE importing services ══
from dotenv import load_dotenv
load_dotenv()  # noqa: E402 — intentionally before service imports

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, status  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from typing import Literal  # noqa: E402

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

    1. Embed the user's question with local Ollama embeddings (query-style prefix).
    2. Search the vector DB for the top-5 most similar chunks.
    3. Build a grounded RAG prompt and get an answer from the local llama3.2:3b model.
    4. Return the answer along with how many source chunks were used.
    """
    if not body.question.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Question must not be empty.",
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
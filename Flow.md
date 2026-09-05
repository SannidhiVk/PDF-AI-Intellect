# Flow.md — PDF AI Intellect: Execution Trace

> This document traces **exactly** how control moves between files and functions for every major user action. Read it top-to-bottom to understand the full call stack, not just what each function does in isolation.

---

## Table of Contents

1. [App Startup](#1-app-startup)
2. [Authentication Flow](#2-authentication-flow)
3. [PDF Upload & Processing](#3-pdf-upload--processing)
4. [Batch Upload (Multiple PDFs)](#4-batch-upload-multiple-pdfs)
5. [RAG Chat (Ask a Question)](#5-rag-chat-ask-a-question)
6. [Summarize a Document](#6-summarize-a-document)
7. [Share Link Flow](#7-share-link-flow)
8. [Share Chat (Public, No Auth)](#8-share-chat-public-no-auth)
9. [Comment System](#9-comment-system)
10. [List / Delete Documents & Batches](#10-list--delete-documents--batches)
11. [Frontend Auth Context Boot](#11-frontend-auth-context-boot)
12. [Key Cross-Cutting Patterns](#12-key-cross-cutting-patterns)

---

## 1. App Startup

> 💡 **Interview Explanation (Say it simply):**
> *"When the FastAPI backend boots up, the primary concern is fail-fast configuration and safe initialization. We explicitly load environment variables via `load_dotenv()` before importing service modules, preventing clients from being initialized with empty keys. During FastAPI's lifespan startup event, we run a sanity check verifying that all critical API keys—Supabase, Groq, and Gemini—are present in the environment. If any key is missing, the server halts immediately with an explicit error rather than failing unpredictably mid-request."*

```
uvicorn app.main:app --reload --port 8000
 |
 +--► Python imports backend/app/main.py
 |    +--► load_dotenv()                         # MUST run first — populates os.environ
 |    +--► import app.auth                       # lazy singleton, no client yet
 |    +--► import app.services.ai_service        # reads GROQ_API_KEY + GEMINI_API_KEY
 |    |    +--► Groq(api_key=...)                # Groq client created at module level
 |    |    +--► genai.Client(api_key=...)        # Gemini client created at module level
 |    +--► import app.services.db_service        # does NOT create Supabase client yet
 |    +--► import app.services.pdf_service       # stateless, no side effects
 |
 +--► FastAPI calls lifespan(app) on startup
      +--► Checks SUPABASE_URL, SUPABASE_KEY, GROQ_API_KEY, GEMINI_API_KEY in os.environ
      +--► Raises RuntimeError if any key is missing -> server refuses to start
      +--► Prints masked key summary to stdout -> yield -> server is live
```

**Key detail:** `load_dotenv()` runs on line 22 of `main.py`, *before* any service
imports on line 35. If the order were reversed, `ai_service.py` would read empty
strings from `os.environ` and create clients with null keys.

---

## 2. Authentication Flow

> 💡 **Interview Explanation (Say it simply):**
> *"We use token-based authentication integrated with Supabase Auth. Every protected route uses FastAPI's dependency injection (`Depends(get_current_user)`). Before the endpoint logic executes, FastAPI intercepts the Bearer JWT from the HTTP Authorization header, verifies it with Supabase, and extracts the verified user UUID. If the token is missing or expired, it immediately returns an HTTP 401 or 403. That user UUID is then passed directly into our database queries as a WHERE filter, guaranteeing strict per-tenant data isolation and preventing cross-user data leakage."*

Every protected endpoint includes `user_id: str = Depends(get_current_user)`.
FastAPI resolves this dependency **before** the handler body runs.

```
HTTP Request: Authorization: Bearer <supabase_jwt>
 |
 +--► FastAPI reads Authorization header
 |    +--► _bearer_scheme (HTTPBearer) extracts the token string
 |         [If header missing -> FastAPI auto-returns HTTP 403]
 |
 +--► auth.get_current_user(credentials)         # app/auth.py
      +--► auth._supabase()                       # lazy-build the Supabase client once
      |    +--► auth._get_supabase_client()       # reads SUPABASE_URL + SUPABASE_KEY
      |         +--► supabase.create_client(url, key)  -> _supabase_client cached
      |
      +--► _supabase_client.auth.get_user(token)  # outbound HTTP call to Supabase
      |    +--► Token valid   -> returns response.user (User object)
      |    +--► Token invalid -> raises Exception -> HTTP 401
      |
      +--► return str(response.user.id)           # UUID string handed to the endpoint
```

**Result:** Every handler receives `user_id` as a plain Python `str` UUID.
All subsequent `db_service.*` calls pass this UUID as a WHERE clause filter
to enforce per-user data isolation.

---

## 3. PDF Upload & Processing

> 💡 **Interview Explanation (Say it simply):**
> *"When a user uploads a PDF, it goes through an in-memory ingestion pipeline without saving temporary files to disk. First, PyMuPDF (fitz) extracts the raw text. Next, our chunker splits the text using structural document markers (such as legal sections or headers), falling back to a recursive character text splitter for long passages to preserve semantic meaning. These chunks are embedded into 768-dimensional vectors using Google Gemini, while an executive summary is generated in parallel using Groq's LLaMA 3.3. Finally, document metadata is saved to Postgres, and the chunks and vectors are bulk-stored in Supabase using `pgvector` for downstream similarity search."*

**Endpoint:** `POST /api/process-pdf`
**File:** `main.py` -> `process_pdf()`

```
Browser (PdfUploader.tsx)
 |  multipart/form-data: file=<PDF bytes>
 |  Authorization: Bearer <jwt>
 |
 +--► main.process_pdf(file, user_id)             # main.py:499
      +--► Validate MIME type / .pdf extension
      +--► await file.read()                       # load entire PDF into memory
      +--► Guard: len(file_bytes) > 20 MB -> HTTP 413
      |
      +--► db_service.create_batch(user_id, title=filename)
      |    +--► _get_service_client()              # db_service.py: lazy Supabase init
      |         +--► Supabase INSERT upload_batches -> returns {id, created_at}
      |
      +--► _process_pdf_file_contents(file_bytes, filename, user_id, batch_id)
           |                                      # main.py:361 (shared helper)
           |
           +--► pdf_service.extract_text_from_pdf(file_bytes)
           |    +--► fitz.open(stream=file_bytes)  # pdf_service.py — no disk I/O
           |         +--► Iterate pages -> page.get_text("text")
           |         +--► return "\n\n".join(text_pages)  # raw text string
           |
           +--► pdf_service.split_text_into_chunks(raw_text, filename)
           |    +--► Try _STRUCTURE_PATTERN.split(text)   # legal structure markers
           |    |    +--► If <=1 section: fallback to text.split("\n\n")  # paragraphs
           |    +--► For each section <= CHUNK_SIZE (2000 chars):
           |    |    +--► Chunk(text=section, metadata={source, section_index, "structural"})
           |    +--► For sections > CHUNK_SIZE:
           |         +--► RecursiveCharacterTextSplitter.split_text(section)
           |              +--► Chunk(text=sub, metadata={..., "recursive_fallback"})
           |    return list[Chunk]
           |
           +--► chunk_texts = [c.text     for c in chunk_objects]
           +--► chunk_metas = [c.metadata for c in chunk_objects]
           |
           +--► ai_service.generate_embeddings_batch(chunk_texts)
           |    +--► _require_gemini_client()      # ai_service.py — guard check
           |    +--► _embed_call(client, model, chunks, config)
           |         +--► @_embedding_retry: exponential backoff on 429
           |         +--► client.models.embed_content(...)   # outbound Gemini API call
           |              +--► returns list[list[float]]  — 768-dim vectors
           |
           +--► ai_service.generate_summary(raw_text)
           |    +--► Truncate text to 30,000 chars
           |    +--► _groq_client.chat.completions.create(...)  # outbound Groq API call
           |         +--► system: _SUMMARY_SYSTEM_PROMPT
           |         +--► user:   "Please summarise the following document:\n\n{text}"
           |         +--► returns markdown executive summary string
           |
           +--► db_service.save_document_metadata(user_id, filename, "", summary, word_count, batch_id)
           |    +--► Supabase INSERT documents -> returns {id, created_at, ...}
           |         document_id = doc_record["id"]
           |
           +--► db_service.store_document_chunks(document_id, chunk_texts, embeddings, metas, batch_id)
                +--► Validate len(chunks) == len(embeddings)
                +--► Build rows: [{document_id, content, embedding, metadata, batch_id}, ...]
                +--► Supabase INSERT document_chunks (bulk)
                     +--► pgvector stores embedding as vector(768)

Response: ProcessPdfResponse { document_id, file_name, chunk_count, summary, batch_id }
```

---

## 4. Batch Upload (Multiple PDFs)

> 💡 **Interview Explanation (Say it simply):**
> *"Batch upload lets users process multiple PDFs into a single workspace collection. In production, third-party LLM rate limits are a key challenge. We solve this by implementing a fail-open loop with rate-budget pacing: we introduce an intentional 1.5-second sleep between processing each file to avoid exhausting Gemini's free-tier per-minute embedding quota, backed by an exponential retry decorator on HTTP 429s. If one PDF fails due to size or corrupted formatting, the loop catches the exception and continues with the other files, returning a partial success report to the user instead of failing the entire batch."*

**Endpoint:** `POST /api/process-batch`
**File:** `main.py` -> `process_batch()`

```
Browser: multipart/form-data — files=[pdf1, pdf2, ...]
 |
 +--► main.process_batch(files, user_id)
      +--► Compute batch title (1 file: filename; N files: "Batch (N files): f1, f2...")
      +--► db_service.create_batch(user_id, title)   -> batch_id
      |
      +--► for each file in files:                   # fail-open: errors per file
           +--► asyncio.sleep(1.5) between files      # avoid Gemini rate limiting
           +--► Validate MIME / extension
           +--► await file.read()
           +--► Guard: > 20 MB -> append to failed_items, continue
           |
           +--► _process_pdf_file_contents(...)       # same helper as single upload
                +--► [same pdf -> embed -> summarize -> db flow as section 3 above]
                     +--► append BatchDocumentItem to succeeded_docs

      +--► If ALL files failed -> db_service.delete_batch() -> HTTP 422
      +--► return ProcessBatchResponse { batch_id, documents, failed, message }
```

**Key difference from section 3:** The 1.5 s sleep between files is deliberate —
Gemini's free-tier embedding quota is per-minute. Back-to-back calls for multiple
PDFs would quickly hit RESOURCE_EXHAUSTED without the pause.

---

## 5. RAG Chat (Ask a Question)

> 💡 **Interview Explanation (Say it simply):**
> *"Our chat endpoint implements a complete Retrieval-Augmented Generation (RAG) architecture. When a user asks a question, we first generate a 768-dimensional query vector using Gemini's embedding model. We then invoke a Postgres RPC function in Supabase to perform cosine similarity search via `pgvector` across all relevant chunks for that document or batch. We format the top-matching chunks with clear source document headers, append the last 6 turns of chat history, and pass the prompt to Groq running LLaMA 3.3. The system prompt strictly constrains the model to the retrieved context, ensuring fast, hallucination-free answers with precise document attribution."*

**Endpoint:** `POST /api/chat`
**File:** `main.py` -> `chat()`

```
Browser (ChatWindow.tsx)
 |  body: { document_id | batch_id, question, chat_history: [...] }
 |
 +--► main.chat(body, user_id)
      |
      +--► Input validation: question not empty, document_id OR batch_id present
      |
      +--► Ownership check:
      |    +--► body.batch_id    -> db_service.get_batch_by_id(batch_id, user_id)
      |    +--► body.document_id -> db_service.get_document_by_id(document_id, user_id)
      |         [Not found or wrong user -> HTTP 404]
      |
      +--► ai_service.generate_query_embedding(body.question)
      |    +--► _embed_call(client, model, query, config)  # single embedding call
      |         +--► Gemini API -> returns list[float] (768-dim query vector)
      |
      +--► Vector similarity search:
      |    +--► If batch_id:
      |    |    +--► db_service.search_similar_chunks_by_batch(batch_id, query_embedding, 8)
      |    |         +--► Primary: Supabase RPC "match_batch_chunks"
      |    |         |    +--► Postgres cosine similarity vs all chunks in batch
      |    |         +--► Fallback (if RPC missing):
      |    |              +--► SELECT id FROM documents WHERE batch_id=...
      |    |              +--► search_similar_chunks_multi(doc_ids, query_embedding)
      |    |                   +--► Supabase RPC "match_document_chunks_multi"
      |    |
      |    +--► If document_id:
      |         +--► db_service.search_similar_chunks(document_id, query_embedding, 5)
      |              +--► Supabase RPC "match_document_chunks"
      |                   +--► returns list[str] (plain chunk texts)
      |
      |    context_chunks = top-K chunks ranked by cosine similarity
      |    [batch: list[{"text":..., "source":filename}], doc: list[str]]
      |
      +--► ai_service.generate_rag_answer(question, context_chunks, chat_history)
           +--► Normalize chunks -> list[(source, text)] pairs
           +--► Group by source -> dict[source -> list[text]]
           +--► Build context_block with "=== SOURCE: ... ===" headers
           +--► messages = [system: _RAG_SYSTEM_PROMPT]
           +--► Inject last 6 chat_history turns
           +--► Append user message: context_block + question
           +--► _groq_client.chat.completions.create(...)  # Groq API call
                +--► returns answer string

Response: ChatResponse { document_id, batch_id, question, answer, sources_used }
```

---

## 6. Summarize a Document

> 💡 **Interview Explanation (Say it simply):**
> *"The summarization endpoint delivers fast, high-level document intelligence. The document's raw text is safely truncated to 30,000 characters to prevent context window overflow, and then dispatched to Groq hosting LLaMA 3.3 using an executive summary prompt. The LLM produces a structured, markdown-formatted overview highlighting core findings and key takeaways in just seconds. This summary is stored alongside document metadata and displayed immediately in the user interface."*

**Endpoint:** `POST /api/summarize`
**File:** `main.py` -> `summarize()`

```
Browser
 |  body: { text: "..." }   OR   { document_id: "...", text: "..." }
 |
 +--► main.summarize(body, user_id)
      +--► Validate: document_id OR text must be present
      +--► Validate: if only document_id (no text) -> HTTP 422
      |    (Summary is always generated from raw text, never re-fetched from DB)
      |
      +--► ai_service.generate_summary(body.text)
           +--► Truncate to 30,000 chars
           +--► Groq API: system=_SUMMARY_SYSTEM_PROMPT + user=document text
                +--► returns markdown summary string

Response: SummarizeResponse { document_id, summary }
```

---

## 7. Share Link Flow

> 💡 **Interview Explanation (Say it simply):**
> *"Document sharing is designed around secure, revocable UUID tokens rather than sequential database IDs. An authenticated owner can generate a unique share link with a 10-day expiration window. When an anonymous guest accesses that link, the backend validates the token format with regex and checks its active status and expiration date via lazy evaluation. We use Supabase's backend service-role client to safely bypass standard Row-Level Security (RLS), granting read-only access to the shared document and its summary without forcing the visitor to register or sign in."*

### Create Share

```
Browser (ShareControls.tsx) — authenticated owner
 |  POST /api/documents/{document_id}/share
 |
 +--► main.create_share(document_id, user_id)
      +--► db_service.create_share(document_id, user_id)
           +--► _get_service_client()          # service-role key bypasses RLS
           +--► SELECT from document_shares WHERE document_id AND created_by=user_id
           |    +--► Exists + inactive -> UPDATE is_active=True -> return row
           |    +--► Exists + active   -> return existing row (idempotent)
           +--► If no row: INSERT {document_id, created_by: user_id}
                +--► Postgres auto-generates share_token (UUID default)

Response: { share_token, share_url (FRONTEND_URL/share/<token>), is_active }
```

### Access Share (public, no auth)

```
Browser (share/[token]/page.tsx) — NO auth required
 |  GET /api/share/{token}
 |
 +--► main.get_share_info(token)
      +--► main._get_active_share(token)       # reused by all /share/* endpoints
           +--► _TOKEN_RE.match(token)          # UUID regex fast-fail (prevents probes)
           +--► db_service.get_share_by_token(token)
           |    +--► Validate token is UUID (uuid.UUID check)
           |    +--► SELECT * FROM document_shares JOIN documents WHERE token+active
           +--► Lazy expiry: if age > SHARE_EXPIRY_DAYS (10 days):
                +--► db_service.revoke_share_by_token(token)
                +--► HTTP 404 "share link has expired"

Response: { document_id, file_name, summary }
```

---

## 8. Share Chat (Public, No Auth)

> 💡 **Interview Explanation (Say it simply):**
> *"Share Chat opens up our RAG pipeline to public, unauthenticated guests who have a valid share link. Instead of checking for a user session JWT, the endpoint validates the share token. It then runs our vector similarity search on the document's `pgvector` chunks, queries Groq for an answer grounded in the document context, and non-blockingly logs the Q&A exchange into a separate shared chat table. This allows guest interaction while keeping the document owner's personal workspace completely private and secure."*

**Endpoint:** `POST /api/share/{token}/chat`
**File:** `main.py` -> `share_chat()`

```
Browser — any visitor with the share URL
 |  body: { question, chat_history }
 |
 +--► main.share_chat(token, body)
      +--► _get_active_share(token)            # validate + expiry check
      +--► document_id = share["document_id"]
      |
      +--► ai_service.generate_query_embedding(body.question)
      |    +--► Gemini API -> 768-dim query vector
      |
      +--► db_service.search_similar_chunks(document_id, query_embedding, 5)
      |    +--► Supabase RPC "match_document_chunks" -> top-5 chunks
      |
      +--► ai_service.generate_rag_answer(question, context_chunks, chat_history)
      |    +--► Groq API -> answer string
      |
      +--► db_service.save_share_chat_messages(token, question, answer)
           +--► INSERT [{role:user}, {role:assistant}] into share_chat_messages
                [Non-fatal — DB failure here does NOT break the chat response]

Response: ChatResponse { document_id, question, answer, sources_used }
```

---

## 9. Comment System

> 💡 **Interview Explanation (Say it simply):**
> *"Our comment system supports collaborative, threaded discussions for both logged-in owners and guest viewers. Comments are stored flat in Postgres with an optional `parent_id` foreign key. To deliver a nested thread structure to the frontend, the backend fetches all rows and reconstructs the reply tree in memory using a clean two-pass hash map algorithm (O(N) runtime). We also enforce role-based moderation on deletions: guests and users can only delete their own comments, but the verified document owner has administrative authority to delete any comment under their document."*

### Post Comment (guest via share link)

```
Browser — any visitor
 |  POST /api/share/{token}/comments
 |  body: { content, author_name, parent_id? }
 |
 +--► main.post_shared_comment(token, body)
      +--► _get_active_share(token)            # 404 if invalid/expired
      +--► Validate content.strip() not empty
      +--► db_service.create_comment(document_id, content, author_name, user_id=None, parent_id)
           +--► _get_service_client()          # service-role to bypass RLS
           +--► Validate parent_id as UUID (guard vs. 22P02 Postgres error)
           +--► INSERT into document_comments
                +--► user_id=NULL (guest), parent_id links to parent comment

Response: CommentResponse { id, parent_id, author_name, content, created_at, is_own=False }
```

### Post Comment (authenticated owner)

```
POST /api/documents/{document_id}/comments  (requires JWT)
 |
 +--► main.post_owner_comment(document_id, body, user_id)
      +--► db_service.create_comment(..., user_id=user_id)
           +--► INSERT with user_id set -> owner's comments flagged is_own=True
```

### How replies nest (thread building)

```
db_service.get_comments(document_id)
 +--► SELECT * FROM document_comments ORDER BY created_at ASC
      +--► returns flat list of rows (no hierarchy)

main._thread_comments(flat, requesting_user_id)
 +--► Pass 1: Build index dict[id -> CommentResponse] for all rows
 +--► Pass 2: For each row with parent_id -> index[parent_id].replies.append(cr)
              Rows without parent_id go into roots[]
              -> returns nested tree (roots with .replies populated)
```

### Delete Comment

```
DELETE /api/comments/{comment_id}  (requires JWT)
 |
 +--► main.delete_comment(comment_id, user_id)
      +--► db_service.delete_comment(comment_id, user_id)
      |    +--► DELETE WHERE id=comment_id AND user_id=user_id
      |         (succeeds only if commenter == caller)
      |
      +--► If not deleted -> db_service.owner_delete_comment(comment_id, user_id)
           +--► JOIN document_comments -> documents to verify doc.user_id == caller
           +--► DELETE WHERE id=comment_id  (owner can delete anyone's comment)
```

---

## 10. List / Delete Documents & Batches

> 💡 **Interview Explanation (Say it simply):**
> *"Document and batch management handles listing user assets and performing clean cascading deletions. When a user deletes a batch or document, we don't just drop the top-level record; we execute an explicit, ordered cascade delete across all dependent tables—removing share links, comments, metadata, and all vector embeddings in `document_chunks`. This explicit cleanup prevents orphaned vector rows from consuming database storage or corrupting subsequent vector similarity searches."*

```
GET /api/batches  (authenticated)
 +--► db_service.get_batches_by_user(user_id)
      +--► SELECT upload_batches + nested documents(...) WHERE user_id=... ORDER BY newest
           +--► main assembles BatchListItem[] from raw dicts

DELETE /api/batches/{batch_id}
 +--► db_service.delete_batch(batch_id, user_id)
      +--► Verify ownership (SELECT id WHERE id+user_id)
      +--► Find all child doc_ids (SELECT id FROM documents WHERE batch_id=...)
      +--► DELETE document_shares    per doc_id
      +--► DELETE document_comments  per doc_id
      +--► DELETE document_chunks    WHERE document_id IN doc_ids
      +--► DELETE documents          WHERE id IN doc_ids
      +--► DELETE document_chunks    WHERE batch_id=batch_id  (orphan cleanup)
      +--► DELETE upload_batches     WHERE id=batch_id+user_id

GET /api/documents
 +--► db_service.get_documents_by_user(user_id)
      +--► SELECT id, file_name, file_url, created_at, summary, word_count ORDER BY newest

DELETE /api/documents/{document_id}
 +--► db_service.delete_document(document_id, user_id)
      +--► Verify ownership
      +--► DELETE document_chunks
      +--► DELETE document_shares
      +--► DELETE document_comments
      +--► DELETE documents row
```

---

## 11. Frontend Auth Context Boot

> 💡 **Interview Explanation (Say it simply):**
> *"On the frontend, Next.js manages authentication state through a global React Context (`AuthProvider`). On boot, it checks localStorage for an existing session and registers Supabase's `onAuthStateChange` listener to automatically propagate logins, logouts, and token refreshes throughout the UI. To prevent Next.js static prerendering build failures caused by missing client environment variables at build time, we wrapped the Supabase client in a lazy JavaScript Proxy pattern. This delays reading environment variables and instantiating the client until an auth or database function is first invoked at runtime."*

```
Next.js app boots -> layout.tsx renders <AuthProvider>
 |
 +--► AuthContext.tsx — AuthProvider useEffect()
      +--► supabase.auth.getSession()
      |    +--► Reads stored session from browser localStorage
      |    +--► setSession(session) + setUser(session.user) + setLoading(false)
      |
      +--► supabase.auth.onAuthStateChange()
           +--► Subscribes to Supabase auth events
           +--► Updates user/session state on sign-in, sign-out, token refresh
           +--► Returns unsubscribe fn -> called in useEffect cleanup

Any component calling useAuth() receives: { user, session, loading }
 +--► Components pass session.access_token as the Bearer token in all API calls
```

### supabaseClient.ts — lazy proxy pattern

```
import { supabase } from "@/lib/supabaseClient"
 |
 +--► supabase.<anything>
      +--► Proxy.get() -> getSupabase()
           +--► If _supabase already exists: return it immediately
           +--► Otherwise: read NEXT_PUBLIC_SUPABASE_URL + NEXT_PUBLIC_SUPABASE_ANON_KEY
                +--► createClient(url, anonKey) -> cache in _supabase -> return
```

This proxy pattern defers URL/key validation until first actual use,
so Next.js static prerender builds don't fail with "Missing env variable" at build time.

---

## 12. Key Cross-Cutting Patterns

> 💡 **Interview Explanation (Say it simply):**
> *"Across the entire architecture, I emphasize four key engineering patterns: first, **Lazy Singletons** for Supabase and AI clients so environment variables are guaranteed to be loaded before instantiation; second, a **Dual-Client Security Model** separating public Anon keys from backend Service-Role keys that bypass RLS for server-side operations; third, a **Multi-Tier Rate Limiting and Resilience Strategy** combining SlowAPI token buckets, exponential backoffs for AI 429 quota exhaustion, and batch sleep delays; and fourth, **Multi-Document Source Attribution in RAG**, where chunks preserve their source document tags so the LLM clearly attributes answers across multi-PDF collections."*

### Lazy Singleton (DB & Auth clients)

```
_supabase_client = None          # module-level in auth.py / db_service.py

_get_client() / _supabase():
 +--► if client is not None: return cached client       # all subsequent calls
 +--► build client from os.environ -> cache -> return   # first call only
```

Both `auth.py` and `db_service.py` use this pattern. It prevents "Invalid API key"
errors that would occur if the client were built at import time, before `load_dotenv()` ran.

### Service-Role vs. Anon Client

| Operation | Client | Reason |
|---|---|---|
| JWT verification (`auth.py`) | anon key | Standard browser-facing auth flow |
| Ownership checks, data reads | service-role | Bypasses RLS — server has no browser JWT |
| INSERT documents / chunks | service-role | `auth.uid()` is NULL server-side |
| Share management (create/revoke) | service-role | Same — PostgREST sees no user session |
| Comment inserts (owner + guest) | service-role | Same root cause |
| Comment delete (own comment) | anon key | WHERE user_id= enforces ownership in SQL |

### Rate Limiting (three layers)

- **SlowAPI** (backend): per-IP token buckets on every endpoint.
  - 5/min for upload, 10/min for summarize, 30/min for chat.
- **Gemini embedding retry** (`@_embedding_retry`): exponential backoff 2s→60s
  retries only on 429/RESOURCE_EXHAUSTED — other errors fail immediately.
- **Batch inter-file sleep** (`asyncio.sleep(1.5)`): spreads embedding calls
  across Gemini's per-minute quota window during multi-PDF batch uploads.

### The Full RAG Pipeline

```
User's question string
       |
       v   ai_service.generate_query_embedding()
       |   -> Gemini API (gemini-embedding-001)
       |   -> 768-dim query vector
       |
       v   db_service.search_similar_chunks*()
       |   -> Supabase pgvector RPC (cosine similarity)
       |   -> top-K most relevant chunk texts (+ source filename for batch)
       |
       v   ai_service.generate_rag_answer()
       |   -> Groups chunks by source document
       |   -> Injects "=== SOURCE: filename ===" headers
       |   -> Groq API (llama-3.3-70b-versatile)
       |
       v   Answer string (markdown-formatted, grounded in context)
```

### Multi-Document Context Attribution

When `search_similar_chunks_multi` or `search_similar_chunks_by_batch` is used,
chunks return as `list[{"text": ..., "source": filename}]`.

`generate_rag_answer()` groups these by source and prepends:
```
=== SOURCE: Resume_A.pdf ===
[Chunk 1]: ...
---
[Chunk 2]: ...

=== SOURCE: Resume_B.pdf ===
[Chunk 1]: ...
```

The LLM's `_RAG_SYSTEM_PROMPT` instructs it to group its answer per source heading,
preventing facts from different PDFs from being blended into one undifferentiated list.

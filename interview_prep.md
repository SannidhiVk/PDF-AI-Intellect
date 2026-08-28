# 🎯 AI Engineering Interview Prep — PDF AI-Intellect

> Based on your actual codebase. Every answer here maps directly to real code you wrote.

---

## 🏗️ What You Built — One-Line Summary

> "I built a full-stack AI-powered PDF assistant. Users upload a PDF → the system extracts text, splits it into smart chunks, embeds those chunks using Google Gemini, stores the vectors in Supabase pgvector, and then lets users chat with the document using RAG (Retrieval-Augmented Generation) powered by Groq's Llama 3.3 70B model."

**Tech Stack:**
- **Backend**: FastAPI (Python) → `app/main.py`
- **AI – Chat & Summarize**: Groq Cloud API (`llama-3.3-70b-versatile`) → `ai_service.py`
- **AI – Embeddings**: Google Gemini (`gemini-embedding-001`, 768 dims) → `ai_service.py`
- **Database + Vector Search**: Supabase + pgvector → `db_service.py`
- **PDF Processing**: PyMuPDF + LangChain → `pdf_service.py`
- **Auth**: Supabase JWT → `auth.py`
- **Frontend**: Next.js → `frontend/src/`

---

## 1️⃣ Bottlenecks — What Could Break at Scale & How You'd Fix It

### Current Bottlenecks in Your Code

#### 🔴 Bottleneck 1: PDF Upload is One Big Sequential Blocking Call
**Where in code:** `main.py` → `process_pdf()` endpoint (lines 209–295)

**What happens today:**
```
Upload → Extract Text → Split Chunks → Generate ALL Embeddings → Generate Summary → Save to DB
```
All of this happens in ONE request. If the PDF is 100 pages, the user waits for everything.

**What you say:**
> "The `/api/process-pdf` endpoint processes everything synchronously — text extraction, chunking, embedding, and summarization all happen in series. For large PDFs, this could easily hit timeout limits (Render free tier has a 30-second timeout). The fix is to make it async — accept the upload, return immediately with a `job_id`, and process in the background using a task queue like **Celery + Redis** or **FastAPI BackgroundTasks**. The frontend would poll for completion."

---

#### 🔴 Bottleneck 2: Embedding is Batched but Still Synchronous
**Where in code:** `ai_service.py` → `generate_embeddings_batch()` (line 72)

**What it does:** Sends ALL chunks in one API call to Gemini. Good for now, but:
- If you have 500 chunks, one batch call may hit Gemini's token/request limits
- If Gemini is slow, the entire upload stalls

**What you say:**
> "I already use Gemini's batch embedding API (`embed_content` with a list), which is better than calling the API once per chunk. But at scale, I'd add chunked batching — split 500 chunks into groups of 100 and embed in parallel using `asyncio.gather()`. I'd also add exponential backoff retries for API rate limits."

---

#### 🔴 Bottleneck 3: Vector Search has No Caching
**Where in code:** `db_service.py` → `search_similar_chunks()` (line 236)

**What happens:** Every chat message goes to Supabase's pgvector for a cosine similarity search. If 100 users ask the same question about the same document, it runs 100 identical vector searches.

**What you say:**
> "I'd add a Redis cache on the query embedding + document_id combination. If the same question was asked for the same document in the last 5 minutes, serve from cache. This dramatically reduces DB load and Gemini embedding API calls."

---

#### 🔴 Bottleneck 4: Chat History Grows Unbounded
**Where in code:** `ai_service.py` → `generate_rag_answer()` (line 215)

```python
for msg in chat_history[-6:]:  # Only last 6 messages kept
```

**What you say:**
> "I already limit chat history to the last 6 turns to avoid blowing up the context window. But there's no summarization of older history. A better approach for very long conversations would be to summarize older turns and pass that summary instead — similar to what ChatGPT does with 'memory'."

---

## 2️⃣ Latency Reduction — What You Did to Make it Fast

### ✅ What You Actually Did (with code proof)

#### ✅ Latency Win 1: Batch Embedding (not one-at-a-time)
**File:** `ai_service.py` line 72–87

```python
def generate_embeddings_batch(chunks: list[str]) -> list[list[float]]:
    response = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=chunks,  # ← Send ALL chunks in ONE API call
        config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSION),
    )
```

**What you say:**
> "Instead of calling the Gemini embedding API once per chunk (N API calls), I send all chunks together in one batched call. For a 20-chunk document, that reduces API roundtrips from 20 to 1, cutting embedding time by ~90%."

---

#### ✅ Latency Win 2: Dimensionality Reduction (768 instead of 3072)
**File:** `ai_service.py` line 38

```python
EMBEDDING_DIMENSION = 768  # Gemini's native is 3072 dims
```

**What you say:**
> "Gemini's `gemini-embedding-001` natively outputs 3072-dimensional vectors. I use the `output_dimensionality=768` parameter (Matryoshka Representation Learning) to truncate to 768 dims. This means: 4x smaller vectors stored in Postgres, 4x faster vector similarity search in pgvector, and 4x less network data transferred. Google normalizes the truncated vectors so accuracy stays high."

---

#### ✅ Latency Win 3: Smart Chunking Strategy (avoid unnecessary splits)
**File:** `pdf_service.py` lines 17–34 and 163–242

```python
CHUNK_SIZE = 2000  # Was 1000 before
```

**What you say:**
> "I increased chunk size from 1000 to 2000 characters. For short documents like resumes (~1900 chars), this keeps the entire document as a SINGLE chunk — meaning I only need ONE embedding call and ONE vector search result. The old 1000-char limit would split a resume right in the middle, requiring the LLM to piece together the answer from two chunks. Bigger chunks = fewer API calls = lower latency."

---

#### ✅ Latency Win 4: Lazy Singleton DB Client
**File:** `db_service.py` lines 37–68 and `auth.py` lines 43–52

```python
_supabase_client: Client | None = None

def _get_client() -> Client:
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client  # Reuse existing connection
    _supabase_client = create_client(url, key)
```

**What you say:**
> "I use a lazy singleton pattern for the Supabase client. The client is created ONCE on the first request and reused for all subsequent requests. Creating a new DB connection per request would add 100–300ms of connection overhead every single time. With the singleton, that overhead only happens once at startup."

---

#### ✅ Latency Win 5: Only fetch 5 context chunks for RAG (not everything)
**File:** `main.py` lines 365–372

```python
RAG_MATCH_COUNT = 5
context_chunks = db_service.search_similar_chunks(
    document_id=body.document_id,
    query_embedding=query_embedding,
    match_count=RAG_MATCH_COUNT,  # Top 5 most relevant only
)
```

**What you say:**
> "During RAG chat, I only retrieve the top 5 most semantically similar chunks. Passing all 50 chunks to the LLM would increase prompt size dramatically, slow down Groq's response time, and use more tokens. 5 is a balanced number — enough context, not too much noise."

---

#### ✅ Latency Win 6: Low Temperature = Faster, More Deterministic Output
**File:** `ai_service.py` lines 165 and 236

```python
temperature=0.2
```

**What you say:**
> "I set temperature to 0.2 instead of the default 1.0. Lower temperature means the model makes faster decisions (less sampling randomness) and produces tighter, more focused answers. This also reduces output length which speeds up time-to-first-token and total generation time."

---

## 3️⃣ Guardrails — Safety & Quality Controls You Implemented

### ✅ Guardrail 1: Input Validation — Only PDF Files Accepted
**File:** `main.py` lines 213–218

```python
if file.content_type not in ("application/pdf", "application/octet-stream"):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(422, "Only PDF files are accepted.")
```

**What you say:**
> "I validate both the MIME type and the file extension. This prevents users from uploading random files (images, executables, etc.) and protects the PDF processing pipeline from crashes."

---

### ✅ Guardrail 2: Empty Question Prevention
**File:** `main.py` lines 344–348

```python
if not body.question.strip():
    raise HTTPException(422, "Question must not be empty.")
```

**What you say:**
> "Empty or whitespace-only questions would waste an API call to Gemini (to generate an embedding) and then a Groq call (to generate an answer). I reject them at the endpoint level before any AI call is made."

---

### ✅ Guardrail 3: LLM Grounding — Answer Only From Document Context
**File:** `ai_service.py` lines 186–189

```python
_RAG_SYSTEM_PROMPT = """
    GROUNDING RULES:
    - Base your answer strictly on the provided context chunks.
    - If the context does not contain the answer, state: 
      "I couldn't find a direct answer to your question in this document."
"""
```

**What you say:**
> "I use a strict system prompt that tells the LLM to ONLY use the provided document chunks — never its training data. If the answer isn't in the document, it must say so explicitly. This prevents the model from hallucinating answers that aren't in the PDF."

---

### ✅ Guardrail 4: Summary Grounding — No Filler or Invented Facts
**File:** `ai_service.py` lines 115–117

```python
_SUMMARY_SYSTEM_PROMPT = """
    GROUNDING RULES:
    - Use ONLY facts explicitly stated in the document.
    - If a section has no relevant content, omit that section entirely 
      rather than writing filler.
"""
```

**What you say:**
> "The summarization prompt explicitly forbids invented content. If a document has no metrics, the model omits the 'Key Data & Figures' section entirely rather than making something up."

---

### ✅ Guardrail 5: PDF Quality Check — Detect Scanned/Garbled PDFs
**File:** `pdf_service.py` lines 95–160

```python
def assess_extraction_quality(text: str) -> dict:
    readable_ratio = readable_chars / length
    if readable_ratio < 0.75:
        return {"is_likely_garbled": True, "reason": "High proportion of non-standard characters..."}
    if avg_word_len > 20:
        return {"is_likely_garbled": True, "reason": "Unusually long unbroken character runs..."}
```

**What you say:**
> "I built a deterministic text quality checker that runs before sending text to the LLM. It checks the ratio of readable characters and average word length. If a PDF is scanned or image-based (not text-extractable), the checker flags it early rather than sending garbage text to the AI and getting a meaningless summary. I intentionally did NOT use the LLM for this check — LLMs are inconsistent at detecting their own bad inputs."

---

### ✅ Guardrail 6: JWT Authentication on All Private Endpoints
**File:** `auth.py` line 66–94

```python
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    response = _supabase().auth.get_user(token)
    if response.user is None:
        raise HTTPException(401, "Invalid or expired access token.")
    return str(response.user.id)
```

**What you say:**
> "Every endpoint that accesses or modifies user data uses a `Depends(get_current_user)` FastAPI dependency. This verifies the Supabase JWT before any business logic runs. If the token is invalid or expired, the user gets a 401 immediately — no data is touched."

---

### ✅ Guardrail 7: Ownership Check — Users Can Only Access Their Own Documents
**File:** `db_service.py` lines 308–333

```python
def get_document_by_id(document_id: str, user_id: str | None = None):
    query = client.table("documents").select("*").eq("id", document_id)
    if user_id:
        query = query.eq("user_id", user_id)  # ← Only your documents
```

**What you say:**
> "Even if a user has a valid JWT, they can't access another user's document by guessing the document UUID. Every document fetch filters by BOTH `document_id` AND `user_id`. If a document exists but belongs to someone else, the API returns 404 — not 403 — so we don't even reveal that the document exists."

---

### ✅ Guardrail 8: UUID Validation Before DB Queries
**File:** `db_service.py` lines 558–563

```python
try:
    _uuid.UUID(str(token))  # Validate before hitting Postgres
except (ValueError, AttributeError, TypeError):
    return None  # Reject malformed tokens early
```

**What you say:**
> "Before sending a share token to Postgres, I validate it's a proper UUID format. If the frontend accidentally sends the string `'undefined'` or a malformed token, Postgres would throw a raw `22P02` error that becomes an ugly 500. I catch it in Python first and return a clean 404."

---

### ✅ Guardrail 9: Summary Text Truncation Before LLM Call
**File:** `ai_service.py` lines 152–153

```python
MAX_SUMMARY_CHARS = 30_000
truncated_text = text[:MAX_SUMMARY_CHARS]
```

**What you say:**
> "Before sending a document to Groq for summarization, I cap the input at 30,000 characters. Groq's Llama model has a context window limit. Without this cap, a huge PDF could overflow the context window and cause an API error. This guardrail ensures we always stay within safe limits."

---

## 4️⃣ Token Optimization — How You Reduced Token Usage

### 🎯 Token Opt 1: Only pass the TOP 5 relevant chunks (not all chunks)
**File:** `main.py` line 365 and `ai_service.py` line 204

**What you say:**
> "Instead of stuffing the entire document into the prompt (which could be 50 chunks = 100,000 tokens), I use vector similarity search to find the 5 most relevant chunks. This reduces the prompt context from potentially 100K tokens to ~2K–4K tokens per query — a 95%+ reduction."

---

### 🎯 Token Opt 2: Input truncation for summaries
**File:** `ai_service.py` line 152

```python
MAX_SUMMARY_CHARS = 30_000
truncated_text = text[:MAX_SUMMARY_CHARS]
```

**What you say:**
> "For document summarization, I truncate the input to 30,000 characters. Most summaries don't need to read the full document end-to-end — the key information is usually in the first portion. This saves tokens without significantly hurting summary quality."

---

### 🎯 Token Opt 3: Limit chat history to last 6 turns
**File:** `ai_service.py` line 215

```python
for msg in chat_history[-6:]:  # Last 6 messages only
```

**What you say:**
> "I only include the last 6 messages (3 user + 3 assistant turns) from chat history. Older context is dropped. This prevents the system prompt + history + context + question from ballooning over time and hitting token limits mid-conversation."

---

### 🎯 Token Opt 4: Chunk Size Optimization (2000 chars, not larger)
**File:** `pdf_service.py` line 34

```python
CHUNK_SIZE = 2000  # Balanced chunk size
CHUNK_OVERLAP = 200  # Small overlap
```

**What you say:**
> "I tuned chunk size to 2000 characters — big enough for a complete idea/paragraph, small enough that 5 chunks fit in the LLM's context comfortably. The 200-character overlap ensures no important information is cut at chunk boundaries, without duplicating too many tokens."

---

### 🎯 Token Opt 5: Smaller Embedding Vectors = Less Data per Request
**File:** `ai_service.py` line 38 and 83

```python
EMBEDDING_DIMENSION = 768  # Instead of full 3072
output_dimensionality=EMBEDDING_DIMENSION
```

**What you say:**
> "While this doesn't directly reduce LLM tokens, embedding vectors at 768 dims instead of 3072 means 4x less data sent to/from Gemini's embedding API per chunk. For a 20-chunk document, that's 80% less embedding data transferred."

---

### 🎯 Token Opt 6: Tight System Prompts (no padding)
**File:** `ai_service.py` lines 174–189

**What you say:**
> "Both my system prompts (summary and RAG chat) use `textwrap.dedent()` to remove indentation whitespace, and are written concisely. Verbose system prompts with unnecessary padding waste tokens on every single API call. My prompts are directive and compact."

---

## 5️⃣ Main Code Segments to Know — What They Do & What They Call

### 📁 File Map

| File | What it does |
|------|-------------|
| `app/main.py` | FastAPI app — all HTTP endpoints, request/response models |
| `app/auth.py` | JWT verification using Supabase |
| `app/services/ai_service.py` | Groq (chat/summary) + Gemini (embeddings) |
| `app/services/pdf_service.py` | PDF text extraction + chunking |
| `app/services/db_service.py` | All Supabase DB operations |

---

### 🔑 Key Code Flows to Explain in Interview

#### Flow 1: User Uploads a PDF
```
POST /api/process-pdf
  ↓ auth.py: get_current_user() → verifies JWT → returns user_id
  ↓ main.py: reads file bytes
  ↓ pdf_service.extract_text_from_pdf(bytes) → PyMuPDF → raw text
  ↓ pdf_service.split_text_into_chunks(text) → structural split + fallback
  ↓ ai_service.generate_embeddings_batch(chunks) → Gemini API → 768-dim vectors
  ↓ ai_service.generate_summary(text) → Groq API → markdown summary
  ↓ db_service.save_document_metadata() → Supabase documents table
  ↓ db_service.store_document_chunks(chunks, embeddings) → Supabase document_chunks
  ↓ Returns: document_id, chunk_count, summary
```

---

#### Flow 2: User Chats with a Document (RAG)
```
POST /api/chat
  ↓ auth.py: get_current_user() → verify JWT
  ↓ db_service.get_document_by_id(doc_id, user_id) → ownership check
  ↓ ai_service.generate_query_embedding(question) → Gemini API → 768-dim vector
  ↓ db_service.search_similar_chunks(doc_id, embedding, match_count=5)
      → Supabase RPC: match_document_chunks → pgvector cosine similarity
  ↓ ai_service.generate_rag_answer(question, context_chunks, chat_history)
      → Groq API with system prompt + last 6 history + 5 chunks + question
  ↓ Returns: answer, sources_used count
```

---

#### Flow 3: Chunking Strategy (Smart 2-Pass Split)
```
pdf_service.split_text_into_chunks(text)
  ↓ Pass 1 (Structural): Regex split on ARTICLE / Section n.n / numbered clauses
     → If no legal structure found, split on "\n\n" paragraph breaks
  ↓ Pass 2 (Fallback): Any section > 2000 chars gets further split by
     RecursiveCharacterTextSplitter (paragraph → sentence → word → char)
  ↓ Returns: list of Chunk objects with metadata (source, section_index, split_method)
```

---

#### Flow 4: Auth Flow
```
Any protected endpoint
  ↓ FastAPI reads Authorization: Bearer <token> header
  ↓ auth.py: _bearer_scheme validates header format (auto-rejects missing headers)
  ↓ auth.py: _supabase().auth.get_user(token) → Supabase validates JWT
  ↓ If invalid: HTTP 401 Unauthorized
  ↓ If valid: returns user.id (UUID string)
  ↓ Endpoint proceeds with user_id
```

---

### 🔑 Key Functions — Know What They Accept and Return

| Function | File | Accepts | Returns |
|----------|------|---------|---------|
| `generate_embeddings_batch(chunks)` | ai_service.py:72 | `list[str]` | `list[list[float]]` (768 dims) |
| `generate_rag_answer(question, chunks, history)` | ai_service.py:192 | str, list, list | str (answer) |
| `generate_summary(text)` | ai_service.py:147 | str | str (markdown summary) |
| `extract_text_from_pdf(file_bytes)` | pdf_service.py:60 | bytes | str |
| `split_text_into_chunks(text)` | pdf_service.py:163 | str | `list[Chunk]` |
| `search_similar_chunks(doc_id, embedding)` | db_service.py:236 | str, list[float] | `list[str]` (top 5 chunks) |
| `store_document_chunks(doc_id, chunks, embeddings)` | db_service.py:178 | str, lists | None |
| `get_current_user(credentials)` | auth.py:66 | JWT token | str (user_id) |

---

## 🗣️ Interview Cheat Sheet — Quick Verbal Answers

**Q: What is RAG?**
> "RAG stands for Retrieval-Augmented Generation. Instead of asking the LLM to remember everything about a document, I first search for the most relevant pieces of that document using vector similarity, then pass only those relevant pieces to the LLM as context. The LLM generates its answer based on what I retrieved — not its training data."

**Q: What is a vector embedding?**
> "An embedding is a list of numbers (a vector) that represents the meaning of a piece of text. Similar-meaning texts produce similar vectors. I use Google Gemini to convert text chunks into 768-dimensional vectors, then store them in Postgres. When a user asks a question, I convert the question into a vector too, and find which stored chunks are mathematically closest — those are the most semantically relevant ones."

**Q: What is pgvector?**
> "pgvector is a Postgres extension that adds vector data types and similarity search operators. My Supabase database uses it. The `<=>` operator computes cosine distance between vectors. I call a Postgres function called `match_document_chunks` via Supabase RPC, which does the vector search inside the database."

**Q: How do you prevent hallucinations?**
> "Two ways: First, strict system prompt instructions that tell the model to use ONLY the provided context chunks and to say 'I couldn't find the answer' if it's not there. Second, I set temperature to 0.2 instead of 1.0, which makes the model more conservative and factual."

**Q: What is the difference between anon key and service role key in Supabase?**
> "The anon key is for client-side requests and respects Row Level Security (RLS) policies. The service role key bypasses RLS entirely. My Python backend uses the service role key for all DB operations because auth.uid() is always NULL for server-side Python requests — RLS would reject everything. I enforce ownership manually in Python code instead."

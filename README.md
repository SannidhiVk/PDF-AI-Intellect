# 📄 PDF Intellect — AI-Powered PDF Assistant & Collaboration Platform

> An enterprise-grade, full-stack RAG (Retrieval-Augmented Generation) document intelligence platform. Upload PDFs, generate instant executive summaries, chat with documents in natural language, share access via public link or email invitation, and collaborate through threaded comment discussions.

---

## 🌟 Key Features

### 🎯 Core Capabilities
- **🔐 User Signup & Authentication**: Secure registration and sign-in powered by Supabase Auth (JWT), with full password hashing and session management.
- **📄 Intelligent PDF Processing**: Automatic text extraction (`PyMuPDF`), token-aware chunking (`langchain`), and vector embedding generation via Google Gemini API (`gemini-embedding-001`).
- **📊 Executive AI Summaries**: Auto-generated structured summaries powered by Groq Cloud API (`llama-3.3-70b-versatile`), complete with key takeaways, topics, metrics, and markdown formatting.
- **💬 Grounded RAG Chat (Q&A)**: Ask complex questions about any document. Answers are strictly grounded in document context via cosine similarity search on Supabase `pgvector`, eliminating AI hallucinations.
- **🔍 Document Management & Search**: Real-time filename filtering, multi-document switching, and single-click document deletion with automatic database cleanup.
- **🔗 Shareable Public Links**: Generate unique share tokens for any document allowing read-only access (summary + chat + comments) without requiring guest authentication.
- **📧 Email Share Invitations**: Directly invite collaborators via email (powered by Brevo API) with custom HTML invitation templates.
- **💬 Threaded Commenting System**: Engage in document discussions with nested replies, relative time formatting, guest comment support, and markdown rendering.
- **🔑 Password Recovery**: Built-in password reset flow with email verification and password update screens.

---

## 🏗️ Architecture & Tech Stack

```
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                            Next.js 15 Frontend                          │
 │      (TypeScript · Tailwind CSS · React Markdown · Lucide Icons)        │
 └────────────────────────────────────┬────────────────────────────────────┘
                                      │  REST API Calls (JWT Bearer Auth)
                                      ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                            FastAPI Backend                              │
 │            (Python 3.10+ · Uvicorn · PyMuPDF · LangChain)              │
 └───────┬───────────────────┬───────────────────┬───────────────────┬─────┘
         │                   │                   │                   │
         ▼                   ▼                   ▼                   ▼
┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
│ Groq Cloud API │  │ Google Gemini  │  │    Supabase    │  │   Brevo API    │
│ (Llama 3.3 70B)│  │(embedding-001) │  │ (Postgres/RLS/ │  │(Email Invites) │
│ Summaries & RAG│  │ 768d Vector Embed │ pgvector DB)   │  │                │
└────────────────┘  └────────────────┘  └────────────────┘  └────────────────┘
```

| Layer | Technology / Package | Purpose |
|---|---|---|
| **Frontend** | Next.js 15, TypeScript, Tailwind CSS | Modern dark glassmorphism dashboard UI |
| **Backend API** | FastAPI, Uvicorn, Pydantic | Asynchronous REST backend & RAG pipeline |
| **LLM Provider** | Groq Cloud API (`llama-3.3-70b-versatile`) | Ultra-fast summary & grounded RAG answer generation |
| **Vector Embeddings** | Google Gemini (`gemini-embedding-001`) | 768-dimensional document and query embeddings |
| **Database & Vector Store**| Supabase (PostgreSQL + `pgvector`) | Relational storage, user auth, and vector search |
| **Email Service** | Brevo API | Transactional email invitations |

---

## 🚀 End-to-End Setup Guide

Follow this guide to get PDF Intellect up and running locally from scratch.

### 📋 Prerequisites

Ensure you have the following installed on your machine:
1. **Node.js**: v18.0.0 or higher ([Download Node.js](https://nodejs.org/))
2. **Python**: v3.10.0 or higher ([Download Python](https://www.python.org/))
3. **Google Gemini API Key**: Free API key from [aistudio.google.com](https://aistudio.google.com/)
4. **Supabase Account**: Free account at [supabase.com](https://supabase.com)
5. **Groq Cloud API Key**: Free API key from [console.groq.com](https://console.groq.com/)
6. **Brevo API Key** *(For email invitations to any email address)*: Free API key from [brevo.com](https://brevo.com/)

---

### Step 1: Configure Supabase Database Schema

1. Log into your **Supabase Dashboard** and create a new project.
2. Go to the **SQL Editor** in your Supabase project dashboard.
3. Paste and run the following complete setup script to create the necessary tables, vector extensions, and functions:

```sql
-- 1. Enable pgvector extension for AI vector search
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Documents table
CREATE TABLE IF NOT EXISTS public.documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    file_name TEXT NOT NULL,
    file_url TEXT DEFAULT '',
    summary TEXT,
    word_count INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Document chunks table (768-dimensional embeddings from gemini-embedding-001)
CREATE TABLE IF NOT EXISTS public.document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(768) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Document share links table
CREATE TABLE IF NOT EXISTS public.document_shares (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
    share_token TEXT NOT NULL UNIQUE DEFAULT encode(gen_random_bytes(16), 'hex'),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. Threaded document comments table
CREATE TABLE IF NOT EXISTS public.document_comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    author_name TEXT NOT NULL DEFAULT 'Anonymous',
    content TEXT NOT NULL,
    parent_id UUID REFERENCES public.document_comments(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. Vector Similarity Search RPC Function
CREATE OR REPLACE FUNCTION match_document_chunks(
    query_embedding VECTOR(768),
    match_count INT DEFAULT 5,
    filter_document_id UUID DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    document_id UUID,
    content TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        dc.id,
        dc.document_id,
        dc.content,
        1 - (dc.embedding <=> query_embedding) AS similarity
    FROM public.document_chunks dc
    WHERE (filter_document_id IS NULL OR dc.document_id = filter_document_id)
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- 7. Enable Row Level Security (RLS)
ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_shares ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_comments ENABLE ROW LEVEL SECURITY;

-- 8. Basic RLS Policies
CREATE POLICY "Users can manage own documents" ON public.documents
    FOR ALL USING (auth.uid() = user_id);

CREATE POLICY "Users can manage own document chunks" ON public.document_chunks
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM public.documents d
            WHERE d.id = document_chunks.document_id AND d.user_id = auth.uid()
        )
    );
```

4. Go to **Project Settings → API** in Supabase and copy:
   - `Project URL` (`SUPABASE_URL`)
   - `anon public key` (`SUPABASE_KEY`)
   - `service_role secret key` (`SUPABASE_SERVICE_KEY`)

---

### Step 2: Obtain Google Gemini API Key

1. Sign in to [Google AI Studio](https://aistudio.google.com/).
2. Create an API key for Google Gemini.
3. Save the API key to use as `GEMINI_API_KEY` in your environment.

---

### Step 3: Backend Setup (FastAPI)

1. Open a terminal and navigate to the `backend` directory:

```bash
cd backend
```

2. Create and activate a Python virtual environment:

```bash
# On Windows
python -m venv .venv
.venv\Scripts\activate

# On macOS/Linux
python3 -m venv .venv
source .venv/bin/activate
```

3. Install required Python packages:

```bash
pip install -r requirements.txt
```

4. Create the `backend/.env` environment file:

```env
# ── Groq API Configuration ──
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_API_KEY=gsk_your_groq_api_key_here

# ── Supabase Configuration ──
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_KEY=your_anon_public_key_here
SUPABASE_SERVICE_KEY=your_service_role_key_here

# ── Google Gemini Embeddings ──
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_EMBEDDING_MODEL=gemini-embedding-001

# ── Brevo Email Invitations ──
BREVO_API_KEY=xkeysib_your_brevo_api_key_here
BREVO_SENDER_EMAIL=no-reply@pdfintellect.com
BREVO_SENDER_NAME=PDF Intellect

# ── Frontend Link ──
FRONTEND_URL=http://localhost:3000
```

5. Start the backend FastAPI dev server:

```bash
uvicorn app.main:app --reload --port 8000
```

The backend API will be available at `http://127.0.0.1:8000` (API Docs at `http://127.0.0.1:8000/docs`).

---

### Step 4: Frontend Setup (Next.js)

1. Open a new terminal window and navigate to the `frontend` directory:

```bash
cd frontend
```

2. Install Node dependencies:

```bash
npm install
```

3. Create the `frontend/.env.local` environment file:

```env
NEXT_PUBLIC_SUPABASE_URL=https://your-project-ref.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_anon_public_key_here
NEXT_PUBLIC_FASTAPI_URL=http://127.0.0.1:8000
```

4. Start the Next.js development server:

```bash
npm run dev
```

5. Open your browser and navigate to `http://localhost:3000`.

---

## 📡 REST API Reference

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/api/process-pdf` | Required | Upload & process a PDF (extract, chunk, embed, summarize) |
| `GET` | `/api/documents` | Required | List all documents owned by authenticated user |
| `DELETE`| `/api/documents/{id}` | Required | Delete document and associated chunks/shares/comments |
| `POST` | `/api/summarize` | Required | Generate structured AI summary for given text/document |
| `POST` | `/api/chat` | Required | RAG-grounded document question answering |
| `POST` | `/api/documents/{id}/share` | Required | Create or retrieve share token for a document |
| `DELETE`| `/api/documents/{id}/share` | Required | Revoke share token for a document |
| `POST` | `/api/documents/{id}/share/invite` | Required | Send share invitation email via Brevo |
| `GET` | `/api/share/{token}` | Public | Fetch document info & summary via share token |
| `POST` | `/api/share/{token}/chat` | Public | Guest RAG chat via share token |
| `GET` | `/api/share/{token}/comments` | Public | List comments on shared document |
| `POST` | `/api/share/{token}/comments` | Public | Post comment/reply on shared document |

---

## 🧪 Project Structure

```
PDF AI-assistent/
├── backend/
│   ├── app/
│   │   ├── auth.py              # Supabase JWT authentication dependency
│   │   ├── config.py            # Environment variable validation
│   │   ├── main.py              # FastAPI endpoints & lifespan handlers
│   │   └── services/
│   │       ├── ai_service.py    # Groq LLM (Summary/RAG) & Gemini Embeddings
│   │       ├── db_service.py    # Supabase DB operations (Service Role Client)
│   │       └── pdf_service.py   # PyMuPDF text extraction & LangChain chunking
│   ├── .env                     # Backend environment secrets
│   └── requirements.txt         # Python dependencies
│
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── auth/            # Sign In, Sign Up, & Password Reset pages
│   │   │   ├── share/[token]/   # Public shared document landing page
│   │   │   ├── page.tsx         # Main authenticated dashboard
│   │   │   └── layout.tsx       # Global root layout & AuthProvider
│   │   ├── components/
│   │   │   ├── ChatWindow.tsx   # Interactive RAG Chat UI
│   │   │   ├── CommentSection.tsx # Threaded discussion component
│   │   │   ├── PdfUploader.tsx  # Drag & drop upload box
│   │   │   ├── Sidebar.tsx      # History sidebar with search & delete
│   │   │   └── SummaryView.tsx  # Structured summary & share modal
│   │   └── lib/
│   │       ├── AuthContext.tsx  # React Context for Supabase Auth state
│   │       └── supabaseClient.ts # Client-side Supabase instance
│   └── package.json             # Frontend dependencies
│
└── README.md                    # Project documentation
```

---

## 🛡️ Security & Privacy

- **Token Validation**: All owner endpoints are protected by `get_current_user` JWT verification using Supabase Auth.
- **Service Role Scoping**: Backend database operations utilize Supabase `service_role` client for elevated vector search and share access while enforcing strict user ownership checks.
- **No Vector Leakage**: Similarity searches are explicitly filtered by `filter_document_id` to prevent cross-document or cross-user context retrieval.
- **Secret Management**: API keys (`GROQ_API_KEY`, `SUPABASE_SERVICE_KEY`, `BREVO_API_KEY`) are kept strictly on the backend inside environment variables and never exposed to the client.

---

## 📄 License

This project is licensed under the MIT License.

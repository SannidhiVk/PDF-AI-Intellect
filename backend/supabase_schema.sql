-- ============================================================
--  PDF AI Assistant – Supabase Database Setup
--  Run these statements in order in the Supabase SQL Editor
--  (Dashboard → SQL Editor → New Query)
-- ============================================================


-- ─────────────────────────────────────────────
-- STEP 1: Enable the pgvector extension
-- ─────────────────────────────────────────────
-- This must be run BEFORE creating any tables that use vector columns.
CREATE EXTENSION IF NOT EXISTS vector;


-- ─────────────────────────────────────────────
-- STEP 2: Create the `documents` table
-- ─────────────────────────────────────────────
-- Stores metadata for each uploaded PDF.
-- `user_id` references Supabase Auth's built-in auth.users table.
CREATE TABLE IF NOT EXISTS documents (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    file_name   TEXT        NOT NULL,
    file_url    TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for fast lookup of all documents belonging to a user
CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);

-- Row-Level Security: users can only access their own documents
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own documents"
    ON documents FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can insert their own documents"
    ON documents FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can delete their own documents"
    ON documents FOR DELETE
    USING (auth.uid() = user_id);


-- ─────────────────────────────────────────────
-- STEP 3: Create the `document_chunks` table
-- ─────────────────────────────────────────────
-- Stores text chunks and their 768-dimensional Gemini embeddings.
-- The vector(768) column uses pgvector for cosine similarity search.
CREATE TABLE IF NOT EXISTS document_chunks (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content     TEXT        NOT NULL,
    embedding   vector(768) NOT NULL
);

-- Index for fast document-level chunk lookup
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON document_chunks(document_id);

-- IVFFlat index for approximate nearest-neighbour search (cosine distance).
-- Tune `lists` to roughly sqrt(total_rows) for best performance.
-- Re-create with a higher `lists` value as your data grows.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON document_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Row-Level Security (inherits access via documents)
ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view chunks of their own documents"
    ON document_chunks FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = document_chunks.document_id
            AND d.user_id = auth.uid()
        )
    );

CREATE POLICY "Users can insert chunks for their own documents"
    ON document_chunks FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = document_chunks.document_id
            AND d.user_id = auth.uid()
        )
    );


-- ─────────────────────────────────────────────
-- STEP 4: Create the vector similarity search RPC function
-- ─────────────────────────────────────────────
-- This function is called from db_service.py via supabase.rpc().
-- It returns the top `match_count` chunks for a given document,
-- ordered by cosine similarity (highest first).
CREATE OR REPLACE FUNCTION match_document_chunks(
    query_embedding   vector(768),
    match_document_id UUID,
    match_count       INT DEFAULT 5
)
RETURNS TABLE (
    id          UUID,
    document_id UUID,
    content     TEXT,
    similarity  FLOAT
)
LANGUAGE sql STABLE
AS $$
    SELECT
        dc.id,
        dc.document_id,
        dc.content,
        -- cosine similarity = 1 - cosine distance
        1 - (dc.embedding <=> query_embedding) AS similarity
    FROM document_chunks dc
    WHERE dc.document_id = match_document_id
    ORDER BY dc.embedding <=> query_embedding  -- ascending distance = descending similarity
    LIMIT match_count;
$$;

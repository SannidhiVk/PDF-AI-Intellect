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
-- Migration 2026-08-17: added metadata JSONB column for chunk provenance
-- (section_index, split_method, source filename). Run on existing deployments:
-- ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS document_chunks (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content     TEXT        NOT NULL,
    embedding   vector(768) NOT NULL,
    metadata    JSONB       NOT NULL DEFAULT '{}'
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
    query_embedding    vector(768),
    filter_document_id UUID,
    match_count        INT DEFAULT 5
)
RETURNS TABLE (
    id          UUID,
    document_id UUID,
    content     TEXT,
    similarity  FLOAT
)
LANGUAGE plpgsql VOLATILE
AS $$
BEGIN
    -- Increase probe count so the IVFFlat index searches more clusters.
    -- Default probes=1 means only 1 cluster is checked, which misses nearly
    -- everything on small datasets (the index has lists=100 but a single PDF
    -- may only contribute 10-30 chunks, all in one cluster).
    SET LOCAL ivfflat.probes = 10;

    RETURN QUERY
        SELECT
            dc.id,
            dc.document_id,
            dc.content,
            1 - (dc.embedding <=> query_embedding) AS similarity
        FROM document_chunks dc
        WHERE dc.document_id = filter_document_id
        ORDER BY dc.embedding <=> query_embedding
        LIMIT match_count;
END;
$$;


-- ─────────────────────────────────────────────
-- STEP 5: Create the `document_shares` table
-- ─────────────────────────────────────────────
-- Each row represents a unique shareable link for one document.
-- share_token is a random UUID used directly in the URL: /share/<share_token>
-- Setting is_active = FALSE effectively revokes the link without deleting it.
CREATE TABLE IF NOT EXISTS document_shares (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    share_token UUID        NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    created_by  UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_shares_document_id ON document_shares(document_id);
CREATE INDEX IF NOT EXISTS idx_shares_token       ON document_shares(share_token);

-- RLS: authenticated owners can manage their own share links.
-- Public (token-validated) reads are done server-side with the service-role key,
-- so they bypass RLS entirely — no anon SELECT policy needed.
ALTER TABLE document_shares ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Owners can manage their share links"
    ON document_shares FOR ALL
    USING (auth.uid() = created_by)
    WITH CHECK (auth.uid() = created_by);

CREATE POLICY "Anyone can view active share links"
    ON document_shares FOR SELECT
    USING (is_active = true);


-- ─────────────────────────────────────────────
-- STEP 6: Create the `document_comments` table
-- ─────────────────────────────────────────────
-- Stores comments and one-level replies on a document.
-- parent_id = NULL  → top-level comment
-- parent_id = <id>  → reply to that comment (one level deep)
-- user_id = NULL    → guest commenter (no Supabase account)
-- author_name       → display name (always set; pulled from profile for auth users)
CREATE TABLE IF NOT EXISTS document_comments (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID        NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_id   UUID        REFERENCES document_comments(id) ON DELETE CASCADE,
    user_id     UUID        REFERENCES auth.users(id) ON DELETE SET NULL,
    author_name TEXT        NOT NULL DEFAULT 'Anonymous',
    content     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_comments_document_id ON document_comments(document_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent_id   ON document_comments(parent_id);

-- RLS: The document owner can SELECT all comments on their docs.
-- Anyone can view / post comments on documents with active share links.
ALTER TABLE document_comments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Document owner can view all comments"
    ON document_comments FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = document_comments.document_id
              AND d.user_id = auth.uid()
        )
    );

CREATE POLICY "Anyone can view comments on active shared documents"
    ON document_comments FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM document_shares s
            WHERE s.document_id = document_comments.document_id
              AND s.is_active = true
        )
    );

CREATE POLICY "Authenticated users can post comments"
    ON document_comments FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Anyone can post comments on active shared documents"
    ON document_comments FOR INSERT
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM document_shares s
            WHERE s.document_id = document_comments.document_id
              AND s.is_active = true
        )
    );

CREATE POLICY "Users can delete their own comments"
    ON document_comments FOR DELETE
    USING (auth.uid() = user_id);

CREATE POLICY "Document owner can delete any comment"
    ON document_comments FOR DELETE
    USING (
        EXISTS (
            SELECT 1 FROM documents d
            WHERE d.id = document_comments.document_id
              AND d.user_id = auth.uid()
        )
    );

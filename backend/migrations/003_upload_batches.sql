-- ============================================================
--  Migration 003: Add upload_batches and batch_id FKs + Backfill
--  Run in: Supabase Dashboard → SQL Editor → New Query
-- ============================================================

-- Step 1: Create the `upload_batches` table
CREATE TABLE IF NOT EXISTS upload_batches (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    title       TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_batches_user_id ON upload_batches(user_id);

ALTER TABLE upload_batches ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view their own batches" ON upload_batches;
CREATE POLICY "Users can view their own batches"
    ON upload_batches FOR SELECT
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can insert their own batches" ON upload_batches;
CREATE POLICY "Users can insert their own batches"
    ON upload_batches FOR INSERT
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can delete their own batches" ON upload_batches;
CREATE POLICY "Users can delete their own batches"
    ON upload_batches FOR DELETE
    USING (auth.uid() = user_id);


-- Step 2: Add batch_id to `documents` table
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS batch_id UUID
    REFERENCES upload_batches(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_documents_batch_id ON documents(batch_id);


-- Step 3: Add batch_id to `document_chunks` table
ALTER TABLE document_chunks
    ADD COLUMN IF NOT EXISTS batch_id UUID
    REFERENCES upload_batches(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_chunks_batch_id ON document_chunks(batch_id);


-- Step 4: Backfill existing data for production safety
-- For every existing document where batch_id IS NULL, create a 1-file upload_batches row
-- and update both documents and document_chunks.
DO $$
DECLARE
    doc_record RECORD;
    new_batch_id UUID;
BEGIN
    FOR doc_record IN
        SELECT id, user_id, file_name, created_at
        FROM documents
        WHERE batch_id IS NULL
    LOOP
        INSERT INTO upload_batches (user_id, title, created_at)
        VALUES (doc_record.user_id, doc_record.file_name, doc_record.created_at)
        RETURNING id INTO new_batch_id;

        UPDATE documents
        SET batch_id = new_batch_id
        WHERE id = doc_record.id;

        UPDATE document_chunks
        SET batch_id = new_batch_id
        WHERE document_id = doc_record.id;
    END LOOP;
END $$;


-- Step 5: Vector similarity search across an entire batch
CREATE OR REPLACE FUNCTION match_batch_chunks(
    query_embedding    vector(768),
    filter_batch_id    UUID,
    match_count        INT DEFAULT 8
)
RETURNS TABLE (
    id          UUID,
    document_id UUID,
    batch_id    UUID,
    content     TEXT,
    similarity  FLOAT
)
LANGUAGE plpgsql VOLATILE
AS $$
BEGIN
    SET LOCAL ivfflat.probes = 10;

    RETURN QUERY
        SELECT
            dc.id,
            dc.document_id,
            dc.batch_id,
            dc.content,
            1 - (dc.embedding <=> query_embedding) AS similarity
        FROM document_chunks dc
        WHERE dc.batch_id = filter_batch_id
        ORDER BY dc.embedding <=> query_embedding
        LIMIT match_count;
END;
$$;

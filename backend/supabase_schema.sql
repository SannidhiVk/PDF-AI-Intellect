-- ============================================================
--  Migration: attach file_name to multi-document RPC results
--  Run in Supabase Dashboard → SQL Editor → New Query
-- ============================================================
-- Why: document_chunks only stores document_id, not the filename.
-- The batch/multi RPCs need to JOIN documents to return file_name so
-- the backend can attribute each retrieved chunk to the right PDF
-- (needed for organizing multi-file chat answers by source document).

-- ─────────────────────────────────────────────
-- match_document_chunks_multi → add file_name
-- ─────────────────────────────────────────────
DROP FUNCTION IF EXISTS match_document_chunks_multi(vector(768), UUID[], INT);

CREATE OR REPLACE FUNCTION match_document_chunks_multi(
    query_embedding     vector(768),
    filter_document_ids UUID[],
    match_count         INT DEFAULT 8
)
RETURNS TABLE (
    id          UUID,
    document_id UUID,
    file_name   TEXT,
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
            d.file_name,
            dc.content,
            1 - (dc.embedding <=> query_embedding) AS similarity
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE dc.document_id = ANY(filter_document_ids)
        ORDER BY dc.embedding <=> query_embedding
        LIMIT match_count;
END;
$$;


-- ─────────────────────────────────────────────
-- match_batch_chunks → add file_name
-- ─────────────────────────────────────────────
DROP FUNCTION IF EXISTS match_batch_chunks(vector(768), UUID, INT);

CREATE OR REPLACE FUNCTION match_batch_chunks(
    query_embedding    vector(768),
    filter_batch_id    UUID,
    match_count        INT DEFAULT 8
)
RETURNS TABLE (
    id          UUID,
    document_id UUID,
    batch_id    UUID,
    file_name   TEXT,
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
            d.file_name,
            dc.content,
            1 - (dc.embedding <=> query_embedding) AS similarity
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE dc.batch_id = filter_batch_id
        ORDER BY dc.embedding <=> query_embedding
        LIMIT match_count;
END;
$$;
-- ============================================================
--  Migration: add created_by to document_shares
--  Run in: Supabase Dashboard → SQL Editor → New Query
--
--  Context:
--    The document_shares table was originally created without the
--    `created_by` column. This migration adds it, back-fills existing
--    rows by copying owner user_id from the parent documents table,
--    then locks it to NOT NULL.
--
--  Safe to re-run: all statements use IF NOT EXISTS / conditional logic.
-- ============================================================

-- Step 1: Add the column as nullable first so existing rows don't violate NOT NULL
ALTER TABLE document_shares
    ADD COLUMN IF NOT EXISTS created_by UUID
    REFERENCES auth.users(id) ON DELETE CASCADE;

-- Step 2: Back-fill existing rows from the parent document's owner
UPDATE document_shares
SET created_by = (
    SELECT user_id
    FROM documents
    WHERE documents.id = document_shares.document_id
)
WHERE created_by IS NULL;

-- Step 3: Now that all rows have a value, enforce NOT NULL
ALTER TABLE document_shares
    ALTER COLUMN created_by SET NOT NULL;

-- Step 4: Add an index for fast owner-scoped lookups (create_share, revoke_share)
CREATE INDEX IF NOT EXISTS idx_shares_created_by ON document_shares(created_by);

-- Step 5: Drop the old catch-all RLS policy (if it exists) and recreate it
--         so it correctly gates on created_by
DROP POLICY IF EXISTS "Owners can manage their share links" ON document_shares;

CREATE POLICY "Owners can manage their share links"
    ON document_shares FOR ALL
    USING (auth.uid() = created_by)
    WITH CHECK (auth.uid() = created_by);

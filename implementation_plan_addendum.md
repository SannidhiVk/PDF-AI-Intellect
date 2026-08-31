# Implementation Plan — Addendum (Finalized Decisions)

This addendum patches 3 gaps identified in the original `implementation_plan.md`
that were not yet decided at the time it was written. Merge these into the
corresponding sections before implementation begins.

---

## Patch 1 — Partial Batch Failure Behavior (→ Component 2, `POST /api/process-batch`)

**Original plan gap:** Did not specify what happens if one file in a multi-file
batch fails to process (e.g. scanned/image-only PDF with no extractable text).

**Decision: Fail-open.**
- Each file is processed independently inside the batch loop.
- A failure on one file does NOT abort the batch or roll back already-succeeded files.
- The endpoint response includes a `failed` array alongside `documents`:
  ```json
  {
    "batch_id": "uuid",
    "title": "...",
    "documents": [ /* succeeded files */ ],
    "failed": [
      { "filename": "scanned_doc.pdf", "error": "No extractable text found." }
    ]
  }
  ```
- **Exception:** if ALL files in the batch fail, the empty batch shell is deleted
  (`db_service.delete_batch`) and the endpoint returns `422` with the full error list —
  no orphaned empty batches are left in the DB.
- **Frontend requirement:** `PdfUploader.tsx` must render a non-blocking warning
  banner/toast when `failed.length > 0`, while still opening the batch normally
  for the files that succeeded.

---

## Patch 2 — Backfill Migration for Existing Data (→ Component 1, Supabase migration)

**Original plan gap:** `ALTER TABLE documents ADD COLUMN batch_id` leaves existing
rows with `batch_id = NULL`, which would make them invisible once the sidebar is
rebuilt around `GET /api/batches`.

**Decision: Backfill required** — this is treated as a production system with
real user data (company evaluation context), not a greenfield/dev-only build.

- Migration `003_upload_batches.sql` must include a backfill step (`DO $$ ... $$`
  block) that creates one 1-file `upload_batches` row per pre-existing document
  with `batch_id IS NULL`, then updates both `documents` and `document_chunks`
  to point at that new batch.
- **Operational requirement:** take a manual export/backup of the `documents`
  and `document_chunks` tables before running this migration, since it rewrites
  `batch_id` on every existing row.
- RLS policies (`SELECT`/`INSERT`/`DELETE` scoped to `auth.uid() = user_id`) must
  be added to `upload_batches` in the same migration — the original plan
  mentioned "add RLS policies" as a bullet without specifying them.

---

## Patch 3 — Summarization Provider Confirmed (→ Component 2, `_process_single_pdf`)

**Original plan gap:** Plan text said "summarizes with Groq" while the stated
project stack (top of conversation) said Gemini 2.0 Flash for all LLM calls —
unresolved inconsistency.

**Decision: Groq is correct.** The stack has changed since the original
architecture rules were written.
- `summary_service.py` (or wherever `generate_summary()` lives) uses the Groq
  SDK/API, not `google-generativeai`.
- Embeddings remain on Gemini `text-embedding-004` (unaffected by this patch —
  only the summarization LLM call changes provider).
- Going forward, all code samples for summary generation will use the Groq
  client, not the Gemini SDK.

---

## Net effect on file-by-file changes

| File | Additional change from addendum |
|---|---|
| `backend/supabase_migrations/003_upload_batches.sql` | Add backfill `DO $$` block + RLS policies (Patch 2) |
| `backend/app/main.py` → `process_pdf_batch` | Fail-open try/except per file, `failed[]` in response, empty-batch cleanup (Patch 1) |
| `backend/app/services/summary_service.py` | Confirm/keep Groq client, no Gemini swap (Patch 3) |
| `frontend/src/components/PdfUploader.tsx` | Render `failed[]` as warning banner, not a blocking error (Patch 1) |

No other sections of the original plan change — Components 1–3 and the
Verification Plan in `implementation_plan.md` remain valid as written.

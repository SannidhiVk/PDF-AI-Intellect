"""
pdf_service.py
--------------
Handles all PDF-related operations:
  - Extracting raw text from an uploaded PDF file bytes using PyMuPDF.
  - Splitting the extracted text into overlapping chunks using LangChain's
    RecursiveCharacterTextSplitter, ready for embedding.

Call chain (where this module fits):
  main.py._process_pdf_file_contents()
    └─► pdf_service.extract_text_from_pdf()   ← Step 1: bytes → raw text string
    └─► pdf_service.split_text_into_chunks()  ← Step 2: raw text → list[Chunk]
  Then ai_service and db_service take over for embedding and storage.
"""

import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF — fast, pure-C PDF parser; no disk I/O needed for in-memory bytes
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ── Configuration ────────────────────────────────────────────────────────────
# CHUNK_SIZE was raised from 1000 → 2000. At 1000, short documents (e.g. a
# ~1,900-char single-page resume) were being split into 2 chunks right in
# the middle of a coherent section (e.g. the "Projects" list), forcing the
# LLM to synthesize a complete list across two separate context blocks with
# overlapping/duplicated text at the seam. Small local models in particular
# were prone to treating the duplicated text as "already covered" and
# truncating their answer early, silently dropping items.
#
# 2000 keeps small/medium documents (resumes, short reports, memos) as a
# SINGLE chunk whenever possible, which sidesteps this class of bug entirely
# for the common case. Larger documents will still be split as expected —
# this only changes the split point, not whether large documents get chunked.
#
# CHUNK_SIZE / CHUNK_OVERLAP are now only used as the FALLBACK for oversized
# sections — see split_text_into_chunks() below. The primary split strategy
# is document-aware (structural), which fixes character-splitters cutting
# straight through legal clauses/paragraphs on documents like contracts.
CHUNK_SIZE = 2000       # characters per chunk (fallback pass only)
CHUNK_OVERLAP = 200     # overlapping characters between consecutive chunks

# ── Legal Document Structure Pattern ─────────────────────────────────────────
# Matches common legal document structure markers: ARTICLE headers,
# Section n.n headers, and numbered clause titles. Extend this per contract
# type as needed (NDAs, MSAs, leases, etc. all vary slightly in formatting).
#
# Uses a zero-width lookahead (?=...) so the split includes the delimiter at
# the START of each section (instead of consuming it), preserving the
# ARTICLE/Section header text in the resulting section strings.
_STRUCTURE_PATTERN = re.compile(
    r"""
    (?=
        ^\s*ARTICLE\s+[IVXLC\d]+.*$   |   # ARTICLE I / ARTICLE 2
        ^\s*Section\s+\d+(\.\d+)*.*$  |   # Section 1 / Section 1.2
        ^\s*\d+\.\d+\s+[A-Z].*$       |   # 1.2 Clause Title
        ^\s*\d+\.\s+[A-Z][^\n]{0,80}$     # 1. Clause Title
    )
    """,
    re.MULTILINE | re.VERBOSE,
)


@dataclass
class Chunk:
    """
    A single text chunk with metadata describing how it was produced.

    Fields:
        text:     The actual chunk text that gets embedded and stored.
        metadata: Dict stored alongside the chunk in Supabase for debugging
                  and citation. Keys: source (filename), section_index,
                  split_method ("structural" | "recursive_fallback"),
                  and sub_chunk_index (only when recursive fallback ran).
    """
    text: str
    metadata: dict = field(default_factory=dict)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Extract all text content from a PDF supplied as raw bytes.

    Uses PyMuPDF (fitz) to open the PDF from an in-memory buffer — no
    temporary files are written to disk. This is important for deployment
    on ephemeral containers (Render free tier) where /tmp may be small.

    The extracted pages are joined with double newlines so that paragraph
    breaks are preserved across page boundaries. Split logic downstream
    (split_text_into_chunks) relies on "\n\n" as a paragraph signal.

    Args:
        file_bytes: The raw binary content of the uploaded PDF file.
                    Comes directly from `await file.read()` in the endpoint.

    Returns:
        A single string containing all extracted text, with pages separated
        by double newlines.

    Raises:
        ValueError: If the PDF is empty or no text could be extracted.
                    This surfaces as HTTP 422 in the calling endpoint.
    """
    text_pages: list[str] = []

    # Open the PDF from an in-memory bytes buffer (no disk I/O needed).
    # The `with` block ensures fitz releases resources even on error.
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        if doc.page_count == 0:
            raise ValueError("The uploaded PDF contains no pages.")

        # Iterate every page; page_num is 1-indexed for human-readable metadata.
        for page_num, page in enumerate(doc, start=1):
            # "text" mode returns plain text with newlines preserved.
            # strip() removes leading/trailing whitespace (common in PDFs).
            page_text = page.get_text("text").strip()
            if page_text:
                # Only append non-empty pages — blank/image-only pages return "".
                text_pages.append(page_text)

    if not text_pages:
        # Happens for image-only PDFs (scanned documents with no embedded text).
        raise ValueError(
            "No extractable text found in the PDF. "
            "The document may be scanned or image-only."
        )

    # Join pages with double newlines. Downstream structural splitter uses
    # "\n\n" as a paragraph boundary signal for non-legal documents.
    return "\n\n".join(text_pages)


def assess_extraction_quality(text: str) -> dict:
    """
    Deterministically flag whether extracted PDF text looks garbled/unreliable
    (e.g. scanned image PDFs with broken OCR, or malformed character encoding).

    This is intentionally NOT delegated to the LLM — small local models are
    unreliable at *conditionally* deciding whether to mention this, and tend
    to leak their reasoning into the output either way. Doing it here keeps
    it deterministic and lets the API/frontend decide how to surface it
    (e.g. a small warning banner above the AI summary), fully independent
    of the summary text itself.

    Three heuristics (applied in priority order):
      1. Readable character ratio < 0.75 → likely garbled OCR/encoding.
      2. Average word length > 20 chars  → broken encoding (no whitespace gaps).
      3. Total text length < 50 chars    → effectively empty document.

    Args:
        text: The extracted PDF text to evaluate.

    Returns:
        {
          "is_likely_garbled": bool,
          "reason": str | None,   # human-readable reason if flagged
        }
    """
    if not text or not text.strip():
        return {"is_likely_garbled": True, "reason": "No text was extracted."}

    stripped = text.strip()
    length = len(stripped)

    # Ratio of alphanumeric + common punctuation vs. everything else.
    # Garbled OCR/encoding artifacts tend to produce a lot of stray symbols.
    readable_chars = sum(
        1 for c in stripped if c.isalnum() or c.isspace() or c in ".,;:!?'\"-()[]/%$€£"
    )
    readable_ratio = readable_chars / length

    # Average "word" length — garbled text often has abnormally long runs
    # of characters with no whitespace (broken encoding) or is mostly
    # single characters (bad OCR spacing).
    words = stripped.split()
    avg_word_len = (sum(len(w) for w in words) / len(words)) if words else 0

    # Heuristic 1: symbol density check.
    if readable_ratio < 0.75:
        return {
            "is_likely_garbled": True,
            "reason": (
                "A high proportion of non-standard characters was detected, "
                "which often indicates a scanned/image-based PDF or a "
                "text-encoding issue rather than clean extracted text."
            ),
        }

    # Heuristic 2: token length check.
    if avg_word_len > 20:
        return {
            "is_likely_garbled": True,
            "reason": (
                "Extracted text contains unusually long unbroken character "
                "runs, which often indicates a PDF encoding/extraction issue."
            ),
        }

    # Heuristic 3: near-empty document.
    if length < 50:
        return {
            "is_likely_garbled": True,
            "reason": "Very little text could be extracted from this PDF.",
        }

    return {"is_likely_garbled": False, "reason": None}


def split_text_into_chunks(text: str, source_filename: str = "") -> list[Chunk]:
    """
    Split text into chunks using a document-aware strategy, with a
    size-based recursive fallback for oversized sections.

    Two-pass strategy
    -----------------
    Pass 1 — Structural split (preferred):
        Regex-splits on legal document markers (ARTICLE, Section n.n, numbered
        clauses) so a clause or paragraph stays intact. Preserves the delimiter
        at the head of each section via the zero-width lookahead in _STRUCTURE_PATTERN.

        If NO structural markers are found (e.g. resumes, memos), falls back
        to splitting on "\n\n" (paragraph breaks), which keeps semantic units
        together better than a raw character count would.

    Pass 2 — Recursive character fallback (for oversized sections only):
        Any structural section still longer than CHUNK_SIZE is split further
        by RecursiveCharacterTextSplitter using priority order:
          paragraph → sentence → word → character
        This pass only runs on sections that need it, so short/medium documents
        get zero character-level splitting and no mid-sentence cuts.

    Metadata stored per chunk:
        source:          Original filename (for RAG attribution in multi-doc chat).
        section_index:   Position of the parent structural section (0-indexed).
        split_method:    "structural" | "recursive_fallback"
        sub_chunk_index: Sub-position within a structural section (fallback only).

    Args:
        text:            The full extracted text from the PDF.
        source_filename: Original filename, stored in each chunk's
                          metadata for citation/debugging purposes.

    Returns:
        A list of Chunk objects (text + metadata dict).

    Raises:
        ValueError: If the input text is empty or only whitespace, or if
                    splitting produced no chunks.
    """
    if not text or not text.strip():
        raise ValueError("Cannot split empty text into chunks.")

    # ── Pass 1a: try structural split (legal documents) ──────────────────────
    # _STRUCTURE_PATTERN.split() produces a list of strings between/at
    # the structural markers. The zero-width lookahead means each marker
    # starts a new element (it is not consumed between elements).
    sections = _STRUCTURE_PATTERN.split(text)
    sections = [s.strip() for s in sections if s and s.strip()]

    if len(sections) <= 1:
        # ── Pass 1b: no legal structure — fall back to paragraph breaks ──────
        # Common in resumes, emails, memos, or any non-legal prose document.
        # "\n\n" is preserved as the page-separator by extract_text_from_pdf().
        sections = [s.strip() for s in text.split("\n\n") if s.strip()]

    if not sections:
        raise ValueError("Text splitting produced no chunks.")

    # ── Pass 2 fallback splitter (only used for oversized sections) ──────────
    # Constructed once outside the loop for efficiency.
    # split priority: paragraph → sentence → word → character
    fallback_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        # Split priority: paragraph → sentence → word → character
        separators=["\n\n", "\n", ". ", " ", ""],
        length_function=len,
    )

    chunks: list[Chunk] = []
    for idx, section in enumerate(sections):
        if len(section) <= CHUNK_SIZE:
            # ── Fits in a single chunk — use as-is ───────────────────────────
            # This is the common path for resumes and short reports where each
            # structural section is already under the size limit.
            chunks.append(Chunk(
                text=section,
                metadata={
                    "source": source_filename,
                    "section_index": idx,
                    "split_method": "structural",
                },
            ))
        else:
            # ── Section too large — apply recursive character splitter ────────
            # Happens for long legal clauses, verbose contract sections, etc.
            # CHUNK_OVERLAP ensures consecutive sub-chunks share 200 chars of
            # context so the LLM isn't presented with hard mid-sentence cuts.
            sub_chunks = fallback_splitter.split_text(section)
            for sub_idx, sub in enumerate(sub_chunks):
                chunks.append(Chunk(
                    text=sub,
                    metadata={
                        "source": source_filename,
                        "section_index": idx,
                        "sub_chunk_index": sub_idx,
                        "split_method": "recursive_fallback",
                    },
                ))

    if not chunks:
        raise ValueError("Text splitting produced no chunks.")

    return chunks
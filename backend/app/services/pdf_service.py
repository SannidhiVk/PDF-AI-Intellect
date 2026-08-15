"""
pdf_service.py
--------------
Handles all PDF-related operations:
  - Extracting raw text from an uploaded PDF file bytes using PyMuPDF.
  - Splitting the extracted text into overlapping chunks using LangChain's
    RecursiveCharacterTextSplitter, ready for embedding.
"""

import fitz  # PyMuPDF
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
CHUNK_SIZE = 2000       # characters per chunk
CHUNK_OVERLAP = 200     # overlapping characters between consecutive chunks


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Extract all text content from a PDF supplied as raw bytes.

    Args:
        file_bytes: The raw binary content of the uploaded PDF file.

    Returns:
        A single string containing all extracted text, with pages separated
        by double newlines.

    Raises:
        ValueError: If the PDF is empty or no text could be extracted.
    """
    text_pages: list[str] = []

    # Open the PDF from an in-memory bytes buffer (no disk I/O needed)
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        if doc.page_count == 0:
            raise ValueError("The uploaded PDF contains no pages.")

        for page_num, page in enumerate(doc, start=1):
            page_text = page.get_text("text").strip()
            if page_text:
                text_pages.append(page_text)

    if not text_pages:
        raise ValueError(
            "No extractable text found in the PDF. "
            "The document may be scanned or image-only."
        )

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

    if readable_ratio < 0.75:
        return {
            "is_likely_garbled": True,
            "reason": (
                "A high proportion of non-standard characters was detected, "
                "which often indicates a scanned/image-based PDF or a "
                "text-encoding issue rather than clean extracted text."
            ),
        }

    if avg_word_len > 20:
        return {
            "is_likely_garbled": True,
            "reason": (
                "Extracted text contains unusually long unbroken character "
                "runs, which often indicates a PDF encoding/extraction issue."
            ),
        }

    if length < 50:
        return {
            "is_likely_garbled": True,
            "reason": "Very little text could be extracted from this PDF.",
        }

    return {"is_likely_garbled": False, "reason": None}


def split_text_into_chunks(text: str) -> list[str]:
    """
    Split a large block of text into overlapping chunks suitable for embedding.

    Uses RecursiveCharacterTextSplitter, which intelligently splits on
    paragraph boundaries → sentence boundaries → word boundaries, ensuring
    chunks are semantically coherent.

    Args:
        text: The full extracted text from the PDF.

    Returns:
        A list of text chunk strings.

    Raises:
        ValueError: If the input text is empty or only whitespace.
    """
    if not text or not text.strip():
        raise ValueError("Cannot split empty text into chunks.")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        # Split priority: paragraph → sentence → word → character
        separators=["\n\n", "\n", ". ", " ", ""],
        length_function=len,
    )

    chunks = splitter.split_text(text)

    if not chunks:
        raise ValueError("Text splitting produced no chunks.")

    return chunks
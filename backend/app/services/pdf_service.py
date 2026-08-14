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
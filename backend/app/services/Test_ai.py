"""
test_ai.py
----------
Simplest possible check: extract text from a real PDF, send it to
generate_embedding(), and confirm the Gemini API key actually works.

Usage:
    python test_ai.py
"""

import ai_service as svc
from pypdf import PdfReader

PDF_PATH = r"C:\Users\sanni\Desktop\PDF AI-assistent\Amulya B E.pdf"


def extract_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() or ""
    return text.strip()


def main():
    print(f"Reading: {PDF_PATH}")
    text = extract_text(PDF_PATH)

    if not text:
        print("[FAIL] No text extracted from PDF — file may be a scanned image with no OCR.")
        return

    print(f"[OK] Extracted {len(text)} characters. Preview:")
    print("   ", text[:200].replace("\n", " "), "...")

    print(f"\nCalling Gemini embedding API using model: {svc.EMBEDDING_MODEL}")
    try:
        embedding = svc.generate_embedding(text[:2000])  # first chunk is enough to test
    except Exception as exc:
        print(f"[FAIL] API call failed: {exc}")
        return

    print(f"[PASS] Got embedding of length {len(embedding)}")
    print("   First 5 values:", embedding[:5])

    if len(embedding) == svc.EMBEDDING_DIMENSION:
        print(f"[PASS] Dimension matches expected {svc.EMBEDDING_DIMENSION}")
    else:
        print(f"[WARN] Dimension is {len(embedding)}, expected {svc.EMBEDDING_DIMENSION}")

    print("\nAPI key is working.")


if __name__ == "__main__":
    main()
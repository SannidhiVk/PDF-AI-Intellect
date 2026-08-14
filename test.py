import fitz  # PyMuPDF

path = r"C:\Users\sanni\Desktop\PDF AI-assistent\Amulya B E.pdf"  # adjust to the actual file path on your machine

with fitz.open(path) as doc:
    print(f"Total pages: {doc.page_count}")
    for i, page in enumerate(doc, start=1):
        text = page.get_text("text").strip()
        print(f"\n--- Page {i}: {len(text)} characters ---")
        print(text[:500])
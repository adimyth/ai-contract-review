import io
from pathlib import Path


def extract_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(content)
    elif suffix in (".docx", ".doc"):
        return _extract_docx(content)
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Please upload a PDF or DOCX file.")


def _extract_pdf(content: bytes) -> str:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=content, filetype="pdf")
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    text = "\n".join(pages).strip()
    if not text:
        raise ValueError("Could not extract text from PDF. The file may be scanned or image-based.")
    return text


def _extract_docx(content: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n".join(paragraphs).strip()
    if not text:
        raise ValueError("Could not extract text from DOCX. The file appears to be empty.")
    return text

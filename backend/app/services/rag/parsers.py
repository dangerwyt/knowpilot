
from io import BytesIO
from pypdf import PdfReader
from docx import Document

SUPPORTED_EXTS = {"md", "txt", "pdf", "docx"}


def extract_text(file_name: str, raw: bytes) -> str:
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext not in SUPPORTED_EXTS:
        raise ValueError(f"不支持的文件类型: {ext}")

    if ext == "md" or ext == "txt":
        text = raw.decode("utf-8")

    elif ext == "pdf":
        reader = PdfReader(BytesIO(raw))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)

    elif ext == "docx":
        doc = Document(BytesIO(raw))
        text = "\n".join(p.text for p in doc.paragraphs)

    return text


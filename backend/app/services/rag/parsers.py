
from io import BytesIO
from pypdf import PdfReader
from docx import Document

from app.services.rag.clean import clean_pdf

SUPPORTED_EXTS = {"md", "txt", "pdf", "docx"}


def extract_text(file_name: str, raw: bytes) -> str:
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext not in SUPPORTED_EXTS:
        raise ValueError(f"不支持的文件类型: {ext}")

    if ext == "md" or ext == "txt":
        text = raw.decode("utf-8")

    elif ext == "pdf":
        reader = PdfReader(BytesIO(raw))
        # 按页保留边界再清洗：页眉靠「跨页重复」识别，拼成大字符串就统计不出来了。
        # 剥掉的是页眉 / 页码 / 目录点线（规则见 clean.py，全是结构判据、不写死文档串）。
        # 兜底：万一清洗后为空（整篇被误判成页眉），退回未清洗文本 —— 宁可脏，不可空。
        pages = [page.extract_text() or "" for page in reader.pages]
        text = clean_pdf(pages) or "\n".join(pages)

    elif ext == "docx":
        doc = Document(BytesIO(raw))
        text = "\n".join(p.text for p in doc.paragraphs)

    return text


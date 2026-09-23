"""切分与向量化：把解析后的文本切成块，写入 Milvus。

切分分两层：
    `split_text`        —— 纯文本递归切分（无表格概念）
    `split_table_aware` —— 表格当单元：短表整表一块；超长表按行切开且每块补「表头+分隔行」

生产入口是 `split_table_aware`：文内没有表格时它内部自动退化为 `_plain`（= `split_text` 的行为）。

⚠️ 表格识别只认**规范 Markdown 表格**：每行以 `|` 开头**且**结尾，且**第 2 行是分隔行**（`|---|---|`）。
上游解析器（pdf / docx）若产出 TSV 或无首尾管道符的表，`table_spans()` 会返回空
⇒ 这里**静默退化为普通切分，不报错**。做解析层归一化时必须遵守这个输出契约。
"""
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.services.rag import get_embedder
from app.services.rag.milvus_client import ingest_chunks, delete_by_document

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64


def _plain(size: int, overlap: int) -> RecursiveCharacterTextSplitter:
    """普通文本切分用的分隔符（不含 markdown 结构）。"""
    return RecursiveCharacterTextSplitter(
        chunk_size=size, chunk_overlap=overlap,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""])


def split_text(text: str, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP) -> list[str]:
    """纯文本递归切分（无表格概念）。行为与改造前一致，只是默认尺寸从 220/80 改成 512/64。"""
    return _plain(chunk_size, chunk_overlap).split_text(text)


# ---------------------------------------------------------------- 表格识别

_TBL_SEP_CHARS = set("|-: \t")


def is_table_row(s: str) -> bool:
    s = (s or "").strip()
    return len(s) >= 2 and s.startswith("|") and s.endswith("|")


def is_table_sep(s: str) -> bool:
    """`|---|---|` 这种分隔行：字符只由 | - : 空白组成。

    这里刻意用**纯 Python 判断字符**而不是正则 `[|\\-:]+` —— 实测在 git-bash 的
    heredoc 里反斜杠会被静默改写成 `/`，于是正则里的 `\\-` 变成 `/-`，`-` 不在字符集里，
    分隔行永远匹配不上，而脚本会安安静静地返回"0 张表"。
    """
    s = (s or "").strip()
    return (len(s) >= 3 and s.startswith("|") and s.endswith("|")
            and all(c in _TBL_SEP_CHARS for c in s))


def table_spans(text: str) -> list[tuple[int, int, list[str]]]:
    """真表格的 (起止字符位置, 行列表)。

    真表格 = 连续的 | 行，且**第 2 行是分隔行**。只出现单行 `| a | b |` 不算 ——
    那更可能是散文里的一句引用，硬当表格反而切错。
    """
    lines = text.split("\n")
    offs, p = [], 0
    for ln in lines:
        offs.append(p)
        p += len(ln) + 1
    spans = []
    i = 0
    while i < len(lines) - 1:
        if is_table_row(lines[i]) and is_table_sep(lines[i + 1]):
            j = i
            while j < len(lines) and is_table_row(lines[j]):
                j += 1
            spans.append((offs[i], offs[j - 1] + len(lines[j - 1]), lines[i:j]))
            i = j
        else:
            i += 1
    return spans


def split_one_table(lines: list[str], size: int) -> list[str]:
    """单张表的切分。

    - 短表 → 整表一块（表头天然带着）
    - 超长表 → 按行切，**每一块都补上「表头 + 分隔行」**

    最后这条是关键：markdown 表切开后，后面的块只剩下 `| 4 | docker-compose... |`，
    读的人（和模型）不知道 `4` 是哪一列 —— 表头必须跟着每一块走。
    """
    whole = "\n".join(lines)
    if len(whole) <= size:
        return [whole]
    head = lines[:2]
    out, buf = [], []
    for ln in lines[2:]:
        if buf and len("\n".join(head + buf + [ln])) > size:
            out.append("\n".join(head + buf))
            buf = []
        buf.append(ln)
    if buf:
        out.append("\n".join(head + buf))
    return out


def is_table_label(chunk: str) -> bool:
    """这一块是不是「紧贴表格前的小标题/标签」——它应该跟着表格走，不该被孤立。

    实测（S8 首版）18 个零信息块**全部**是这种：抽出表格时把前面的标题单独留下了，
    例如 `内存估算：` / `### 8. 四处降级路径...` / `--- ## 调研链路详解`，
    它们的下一块正好就是自己所属的表 —— 即标题和表被拆散了。

    判定：很短（<=60 字）且（每行都是标题行 或 整块就是一行短文字）。
    """
    s = (chunk or "").strip()
    if not s or len(s) > 60:
        return False
    lines = [l.strip() for l in s.split("\n") if l.strip()]
    if not lines:
        return False
    if all(l.startswith("#") for l in lines):
        return True
    return len(lines) == 1 and len(s) <= 40


def split_table_aware(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """表格当单元：表内不跨块（短表）；超长表逐块补表头；其余文本走普通切分。

    额外一步：紧贴表格前的短标签（`内存估算：`、`### 8. 四处降级路径`）**并入表格块**。
    """
    spans = table_spans(text)
    if not spans:
        return _plain(size, overlap).split_text(text)
    prose_re = _plain(size, overlap)
    out: list[str] = []
    pos = 0
    for a, b, lines in spans:
        label = ""
        if a > pos:
            pcs = [c for c in prose_re.split_text(text[pos:a]) if c.strip()]
            if pcs and is_table_label(pcs[-1]):
                label = pcs.pop()              # 摘出来，跟着表格走
            out += pcs
        tchunks = split_one_table(lines, size)
        if label:
            if len(label) + 1 + len(tchunks[0]) <= int(size * 1.2):
                tchunks[0] = label + "\n" + tchunks[0]   # 小标签并进表格块
            else:
                out.append(label)                        # 并进去会超长 ⇒ 留在原地
        out += tchunks
        pos = b
    if pos < len(text):
        out += [c for c in prose_re.split_text(text[pos:]) if c.strip()]
    return out


# ---------------------------------------------------------------- 对外入口

def embed_text(text: str) -> tuple[list[str], list[list[float]]]:
    """切块 + 向量化。纯准备动作，失败时 Milvus 分毫未变。

    batch=10：`DashScopeEmbeddings.embed_documents` 是**整个 list 一次性提交**
    （langchain_community/embeddings/dashscope.py 里 `input=texts`）。块从 220 字改到
    512 字后，batch=20 单请求约 10240 字，有超单请求上限的风险；10 约 3020 字，
    比改造前（20×220≈4400）还保守。
    """
    chunks = split_table_aware(text)
    embedder = get_embedder()
    vectors = []
    batch = 10
    for i in range(0, len(chunks), batch):
        vectors.extend(embedder.embed_documents(chunks[i:i + batch]))
    return chunks, vectors

def reingest_text(kb_id: str, document_id: str, text: str, metadata: dict | None = None) -> int:
    """安全重灌：先算（可失败）→ 再删（破坏性）→ 后写（本地）。"""
    chunks, vectors = embed_text(text)
    delete_by_document(document_id)
    return ingest_chunks(kb_id, document_id, chunks, vectors, metadata)

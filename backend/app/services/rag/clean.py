"""PDF 解析清洗：剥掉**页眉 / 页码 / 目录点线**三类零信息噪声。

背景（2026-09-22 实测《冥晨点餐中台系统用户手册》，87 块）
    `extract_text()` 出来的一坨直接进切分器，没有任何清洗阶段。后果有三层：
      ① 页眉「冥晨点餐中台系统 V1.0」+ 裸页码 + 「第 N 页 共 M 页」三行一组、每页重复
         ⇒ **52/87 块含页眉串、全库出现 72 次**，同一串把 52 个块「抱」在一起，
            而真正的内容只占 512 字块的 ~10%。
      ② 目录条目被切成 **22 个空壳块（25% 容量）**，块内只有「章节名 + 一长串点」。
      ③ 后果不只是"脏"，而是**抢检索名额**（实测，见 `docs/踩坑记录.md`）：
            查询「怎么新建门店」→ top2 是空壳块 `(1) 新建门店 ........`（cos **0.7148**），
            比同题的真正文（0.7107）还高 —— 因为空壳块只含章节名，语义"更纯"。

⚠️ 输出契约（硬约束）
    本模块**只删整行**或**做行内替换**，绝不合并 / 重排行、绝不改动 `|` 开头的行。
    上游 `ingest.split_table_aware()` 靠「每行以 `|` 开头且结尾 + 第 2 行是分隔行」
    识别表格；破坏行结构会让表格识别**静默失效**（它不报错，只是退化成普通切分）。

⚠️ 所有阈值都是「结构判据」而非写死的字符串
    比如页眉不写死「冥晨点餐中台系统」，而是「一个短行出现在 ≥60% 的页面上」——
    换文档不用改代码。反过来：**照某一份文档写死串的判据只能待在验收脚本里，不能进生产**
    （`t83` 的 D1 会查"入库文件是否引用了不入库的 playground 工具"，这里刻意不写路径）。
"""
from __future__ import annotations

import math
import re

# 「第 3 页 共 50 页」（左右两侧出现都行 —— 有文档把它和页眉拼在同一行）
# ⚠️ 页码必须用**捕获组**取，不能事后 `re.sub(r"\D", "", ...)` 抠数字 ——
#    「第 1 页 共 3 页」那样会把两个数字拼成 `13`，于是"裸页码 1"永远匹配不上，
#    裸页码静默残留（2026-09-22 由 `pdf_clean_ab.py --selftest` 的 S3b 抓到）。
_RULER = re.compile(r"第\s*(\d+)\s*页\s*共\s*\d+\s*页")
# 目录条目：点线（≥4 个中英文句点/间隔号）+ 结尾的页码或 Word 的「错误！未定义书签」
_TOC_ENTRY = re.compile(r"[.。·]{4,}\s*(?:\d{1,3}|错误！未定义书签。?)\s*$")
_DOTLEAD = re.compile(r"[.。·]{4,}")
_TOC_HEADING = re.compile(r"^目\s*录$")

HEADER_RATIO = 0.6        # 一个短行出现在 ≥60% 的页上 ⇒ 判为页眉
HEADER_MAX_LEN = 40       # 超过这个长度不可能是页眉（那是正文段落）
MIN_PAGES = 3             # 页数 < 3 不做跨页统计：短文档里"重复行"更可能是正文
TOC_MIN_RUN = 3           # 连续 ≥3 行目录条目才算「目录段」；不足则只清点线


def _strip_ruler(page: str) -> tuple[list[str], int | None]:
    """行内剔除「第 N 页 共 M 页」，返回 (行列表, 该页页码)。

    用 `sub` 而不是整行删除：`冥晨点餐中台系统 V1.0 第 1 页 共 50 页` 这种
    「页眉+页码拼成一行」的情况必须保留前半截，留给后面的页眉统计去认。
    """
    m = _RULER.search(page)
    return [_RULER.sub("", ln).rstrip() for ln in page.split("\n")], (
        int(m.group(1)) if m else None)


def _repeat_lines(pages_lines: list[list[str]]) -> set[str]:
    """跨页重复出现的短行 = 页眉（或页脚里的固定串）。

    只有 3 条护栏同时满足才算：
      · 页数 ≥ MIN_PAGES（短文档不判）
      · 行长度 ≤ HEADER_MAX_LEN
      · 出现在 ≥ HEADER_RATIO 的**页面**上（同一页出现多次只记一页）
    `|` 开头的行直接跳过 —— 那是表格，必须原样保留。
    """
    if len(pages_lines) < MIN_PAGES:
        return set()
    need = max(2, math.ceil(len(pages_lines) * HEADER_RATIO))
    seen: dict[str, int] = {}
    for lines in pages_lines:
        for s in {ln.strip() for ln in lines}:
            if s and len(s) <= HEADER_MAX_LEN and not s.startswith("|"):
                seen[s] = seen.get(s, 0) + 1
    return {s for s, n in seen.items() if n >= need}


def _drop_toc_runs(lines: list[str]) -> list[str]:
    """删掉连续 ≥TOC_MIN_RUN 行的「目录段」（连标题行「目录」一起删）。

    不足 TOC_MIN_RUN 的孤立目录条目**不删整行**，只把点线+页码清掉、保留标题文字
    —— 防止把正文里偶然出现的「见附录 .......」，当成目录整行干掉。
    """
    flags = [bool(_TOC_ENTRY.search(ln.strip())) for ln in lines]
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if flags[i]:
            j = i
            while j < n and (flags[j] or not lines[j].strip()):
                j += 1
            if sum(flags[i:j]) >= TOC_MIN_RUN:
                while out and not out[-1].strip():
                    out.pop()                       # 先退掉空行
                if out and _TOC_HEADING.match(out[-1].strip()):
                    out.pop()                       # 再退掉「目录」标题
                i = j
                continue
            out.append(_DOTLEAD.sub(" ", lines[i]).rstrip())
            i += 1
            continue
        out.append(lines[i])
        i += 1
    return out


def clean_pdf(pages: list[str], *, header_ratio: float = HEADER_RATIO,
              header_max_len: int = HEADER_MAX_LEN, drop_toc: bool = True) -> str:
    """把 `PdfReader` 逐页 `extract_text()` 的结果清洗成一段文本。

    ⚠️ 入参必须是**按页分开**的 list，不能事先拼成一个大字符串 ——
       页眉靠"跨页重复"识别，页边界丢了就没法统计（页数的另一个用途是
       判断"这页的裸数字是不是页码"）。
    """
    pages_lines, page_nos = [], []
    for p in pages:
        lines, no = _strip_ruler(p or "")
        pages_lines.append(lines)
        page_nos.append(no)
    headers = {s for s in _repeat_lines(pages_lines) if len(s) <= header_max_len}

    kept: list[str] = []
    for lines, no in zip(pages_lines, page_nos):
        for ln in lines:
            s = ln.strip()
            if s.startswith("|"):                   # 表格行 / 分隔行：一律不动
                kept.append(ln.rstrip())
                continue
            if not s:                               # 空行保留：它是段落分隔符
                kept.append("")
                continue
            if s in headers:                        # 页眉
                continue
            if no is not None and s.isdigit() and int(s) == no:
                continue                            # 裸页码（值 == 本页页码才删）
            kept.append(ln.rstrip())

    if drop_toc:
        kept = _drop_toc_runs(kept)
    return "\n".join(kept).strip("\n")

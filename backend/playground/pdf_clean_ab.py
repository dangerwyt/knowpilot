"""PDF 清洗的**离线 A/B 验收台** —— 用原 PDF 跑「清洗前 vs 清洗后」，不碰 Milvus。

为什么要离线：清洗会改变切分结果 ⇒ 要重灌 Milvus 才生效，而重灌是 live 数据的破坏性动作。
先把「清洗到底改善了没有、有没有删过头」测干净，再决定要不要重灌。

判据分两组，**两组都必须能 FAIL**：

  正向（噪声归零）
    F1 含页眉串的块占比        60% → 0%
    F2 页眉串全库出现次数       72 → 0
    F3 目录空壳块数             20 → 0
    F4 页码串「第N页共M页」次数 70 → 0
    F5 块总数**不上升**         （清洗若把内容切碎，块数会涨）

  反向（防清洗过头）—— ⚠️ 这才是关键。
    R1 全部**关键句锚点**必须原样存活
    R2 全部**正文章节标题**必须存活（`1、登录` / `5、促销中心` …）
    R3 `|` 开头的表格行数**不变**（清洗绝不能碰表格契约）

  ⚠️ `pdf_noise_check.py` 只有正向那一半 —— 而"把整篇删光"能让正向全绿。
     反向判据就是补这个洞。

用法：
  ./.venv/Scripts/python.exe playground/pdf_clean_ab.py                 # 默认那份手册
  ./.venv/Scripts/python.exe playground/pdf_clean_ab.py --show          # 附清洗后开头原文
  ./.venv/Scripts/python.exe playground/pdf_clean_ab.py --selftest      # 不依赖 PDF，自检判据
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

from app.services.rag import clean as clean_mod  # noqa: E402
from app.services.rag.ingest import split_table_aware  # noqa: E402

DEFAULT_PDF_GLOB = "storage/*/be1d4f37*.pdf"

# ⚠️ 以下三组锚点都是**照这一份手册写死的**（和 pdf_noise_check.py 同样的问题）。
#    换文档必须换掉，否则判据恒绿 ⇒ 看起来"没问题"，其实什么都没测。
HEADER_TOKEN = "冥晨点餐中台系统"                       # 页眉串
RULER = re.compile(r"第\s*\d+\s*页\s*共\s*\d+\s*页")     # 页码串

ANCHORS_BODY = [                                       # R1：正文关键句（含业务值/URL）
    "https://member.hefumian.com/hf-console/#/login",
    "上海冥晨网络科技有限公司",
    "021-63286528",
    "订单编号/手机号",
    "POS 未接单",
    "点击新增分类",
    "尺寸需小于 300KB",
    "保存门店信息后，暂不同步门店信息",
    "应立即回滚上一次版本",
]
# R2：正文章节标题 —— ⚠️ **取自正文，不是目录**。
#    这份文档的目录和正文编号**不一致**：目录写「6、操作日志 / 7、系统管理 / 8、操作指南」，
#    正文实际是「6、营销中心 / 7、操作日志 / 8、系统管理 / 9、操作指南」（多一个「营销中心」）。
#    照目录抄锚点 ⇒ 3 条恒假、R2 假红（2026-09-22 实测踩到）。脚本已加"锚点必须先在原文出现"的自检。
ANCHORS_TITLE = [
    "1、登录", "2、订单中心", "3、主档中心", "4、菜单中心", "5、促销中心",
    "6、营销中心", "7、操作日志", "8、系统管理", "9、操作指南",
]
# F7：**只在目录里出现过**的串 ⇒ 清洗后必须消失。
#     它比"点线计数"更能证明「目录那一段真的被整段删掉了」，而不是"本来就没扫到"。
#     ⚠️ 取串时要把目录的排版空格一起带上（`6、 操作日志` 有空格，正文标题 `7、操作日志` 没有）
#        —— 空格放过一次就会写成恒假判据，跟 R2 是同一个坑。
TOC_ONLY = ["6、 操作日志", "8、 操作指南", "错误！未定义书签"]

_DIGITS = re.compile(r"[\s\d、,，.。:：()（）/|]")


def is_toc_line(line: str) -> bool:
    return bool(clean_mod._TOC_ENTRY.search(line.strip()))  # noqa: SLF001


def is_shell(chunk: str) -> bool:
    """空壳块：去掉页眉/页码串后，**每一行**都只是目录条目。"""
    body = RULER.sub("", chunk).replace(HEADER_TOKEN, "").replace("V1.0", "")
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    return bool(lines) and all(is_toc_line(l) for l in lines)


def is_empty_like(chunk: str) -> bool:
    """剥掉页眉/页码后几乎没剩东西的块（纯噪声块）。"""
    body = RULER.sub("", chunk).replace(HEADER_TOKEN, "").replace("V1.0", "")
    return len(_DIGITS.sub("", body)) < 20


def stats(chunks: list[str]) -> dict:
    hdr = [c for c in chunks if HEADER_TOKEN in c]
    return {
        "块数": len(chunks),
        "含页眉串块数": len(hdr),
        "含页眉串占比": f"{len(hdr) / len(chunks):.0%}",
        "页眉串出现次数": sum(c.count(HEADER_TOKEN) for c in chunks),
        "页码串出现次数": sum(len(RULER.findall(c)) for c in chunks),
        "目录空壳块数": sum(1 for c in chunks if is_shell(c)),
        "近空块数": sum(1 for c in chunks if is_empty_like(c)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF 清洗前后对比（离线，不碰 Milvus）")
    ap.add_argument("--pdf", default=None, help=f"PDF 路径（默认按 glob 找：{DEFAULT_PDF_GLOB}）")
    ap.add_argument("--show", action="store_true", help="打印清洗后的开头原文")
    ap.add_argument("--selftest", action="store_true", help="用构造数据自检判据能否 FAIL")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    pdf = Path(args.pdf) if args.pdf else next(iter(sorted(BACKEND.glob(DEFAULT_PDF_GLOB))), None)
    if not pdf or not Path(pdf).exists():
        print(f"找不到 PDF：{pdf}")
        return 2

    from pypdf import PdfReader
    reader = PdfReader(str(pdf))
    pages = [p.extract_text() or "" for p in reader.pages]
    before = "\n".join(pages)                      # = 现在 parsers.py 的产出
    after = clean_mod.clean_pdf(pages)             # = 清洗后的产出

    b_chunks = split_table_aware(before)
    a_chunks = split_table_aware(after)
    bs, as_ = stats(b_chunks), stats(a_chunks)

    print(f"PDF：{Path(pdf).name}   页数 {len(pages)}   字符 {len(before)} → {len(after)}"
          f"（{len(after) / max(1, len(before)):.0%}）\n")
    print(f"{'指标':<20}{'清洗前':>12}{'清洗后':>12}{'期望':>14}")
    print("-" * 60)
    expect = {"块数": "不上升", "含页眉串块数": "0", "含页眉串占比": "0%",
              "页眉串出现次数": "0", "页码串出现次数": "0", "目录空壳块数": "0", "近空块数": "0"}
    for k in bs:
        print(f"{k:<20}{bs[k]:>12}{as_[k]:>12}{expect[k]:>14}")

    # ---------------- 判据
    rows: list[tuple[str, bool | None, str]] = []

    def rec(cid: str, ok: bool | None, why: str) -> None:
        rows.append((cid, ok, why))

    rec("F1", as_["含页眉串块数"] == 0,
        f"含页眉串的块 {bs['含页眉串块数']} → {as_['含页眉串块数']}（现在 {bs['含页眉串占比']}，目标 0）")
    rec("F2", as_["页眉串出现次数"] == 0, f"页眉串出现 {bs['页眉串出现次数']} → {as_['页眉串出现次数']}")
    rec("F3", as_["目录空壳块数"] == 0, f"目录空壳块 {bs['目录空壳块数']} → {as_['目录空壳块数']}")
    rec("F4", as_["页码串出现次数"] == 0, f"页码串 {bs['页码串出现次数']} → {as_['页码串出现次数']}")
    rec("F5", as_["块数"] <= bs["块数"], f"块数 {bs['块数']} → {as_['块数']}（不上升）")

    miss_b = [a for a in ANCHORS_BODY if a not in after]
    bad_b = [a for a in ANCHORS_BODY if a not in before]      # 锚点自身就不在原文 ⇒ 判据失效
    rec("R1", not miss_b and not bad_b,
        f"正文关键句存活 {len(ANCHORS_BODY) - len(miss_b)}/{len(ANCHORS_BODY)}"
        + (f"；⚠️ 锚点本身不在原文（判据写错了，不是清洗的问题）：{bad_b}" if bad_b else "")
        + (f"；被清洗删掉：{miss_b}" if miss_b else ""))
    miss_t = [a for a in ANCHORS_TITLE if a not in after]
    bad_t = [a for a in ANCHORS_TITLE if a not in before]
    rec("R2", not miss_t and not bad_t,
        f"章节标题存活 {len(ANCHORS_TITLE) - len(miss_t)}/{len(ANCHORS_TITLE)}"
        + (f"；⚠️ 锚点本身不在原文：{bad_t}" if bad_t else "")
        + (f"；被清洗删掉：{miss_t}" if miss_t else ""))
    nrow_b = sum(1 for l in before.split("\n") if l.strip().startswith("|"))
    nrow_a = sum(1 for l in after.split("\n") if l.strip().startswith("|"))
    rec("R3", nrow_b == nrow_a, f"表格行数 {nrow_b} → {nrow_a}（必须不变）")

    dot_b = len(clean_mod._DOTLEAD.findall(before))         # noqa: SLF001
    dot_a = len(clean_mod._DOTLEAD.findall(after))          # noqa: SLF001
    rec("F6", dot_a == 0, f"目录点线出现 {dot_b} → {dot_a}")
    toc_b = {k: before.count(k) for k in TOC_ONLY}
    toc_a = {k: after.count(k) for k in TOC_ONLY}
    rec("F7", all(v == 0 for v in toc_a.values()) and all(v > 0 for v in toc_b.values()),
        f"目录特有串 {toc_b} → {toc_a}（原>0、清洗后=0 才算数）")

    print()
    for cid, ok, why in rows:
        tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        print(f"[{tag}] {cid}  {why}")
    npass = sum(1 for _, ok, _ in rows if ok)
    nfail = sum(1 for _, ok, _ in rows if ok is False)
    print(f"\nPASS={npass}/{len(rows)}  FAIL={nfail}")

    if args.show:
        print("\n── 清洗后开头 1200 字 ──")
        print(after[:1200])
    return 1 if nfail else 0


# ---------------------------------------------------------------- 判据自检

def selftest() -> int:
    """不依赖 PDF：证明每个判据**能算对 True 也能算对 False**（否则就是恒真的凑数判据）。"""
    rows: list[tuple[str, bool, str]] = []

    def rec(cid: str, ok: bool, why: str) -> None:
        rows.append((cid, ok, why))

    # S1 页眉识别：3 页里出现 2 页的短行应被判为页眉；只出现 1 页的不应
    body = ["正文甲", "正文乙", "正文丙"]
    pages = [[f"页眉X", body[0]], ["页眉X", body[1]], ["页眉X", body[2]]]
    rep = clean_mod._repeat_lines(pages)  # noqa: SLF001
    rec("S1a", "页眉X" in rep, f"跨页重复行被认作页眉（实得 {sorted(rep)}）")
    only1 = [[f"只出现一次A"], [], []]
    rec("S1b", not clean_mod._repeat_lines(only1), "只出现 1 页的短行**不**当页眉")

    # S2 目录条目正则：真目录行匹配、正文行不匹配
    rec("S2a", is_toc_line("2、 订单中心 ....................... 4"), "目录行被识别")
    rec("S2b", not is_toc_line("2、订单中心展示了当日订单信息"), "正文行不被误认成目录")

    # S3 clean_pdf 端到端（构造 3 页）：页眉 / 裸页码 / 页码串 全清掉，正文留下
    fake = [
        "页眉Y\n1\n第 1 页 共 3 页\n正文甲",
        "页眉Y\n2\n第 2 页 共 3 页\n正文乙",
        "页眉Y\n3\n第 3 页 共 3 页\n正文丙",
    ]
    out = clean_mod.clean_pdf(fake)
    rec("S3a", "页眉Y" not in out, f"页眉已清（实得 {out!r}）")
    rec("S3b", "第 1 页" not in out and "\n1\n" not in "\n" + out + "\n", "页码串与裸页码已清")
    rec("S3c", all(t in out for t in body), "三页正文全部保留")

    # S4 反向判据真的会红：把正文挖掉一段，锚点检查必须报 FAIL
    broken = out.replace("正文乙", "")
    rec("S4", any(t not in broken for t in body), "锚点缺失时反向判据会 FAIL（不是恒绿）")

    # S5 页数不足时不做页眉统计（防短文档误删）
    rec("S5", not clean_mod._repeat_lines([["重复行"], ["重复行"]]), "页数 < 3 时不做跨页统计")

    # S6 表格行不被碰（页眉/页码照清，`|` 行一行不少）
    tbl = "| # | 功能 |\n|---|---|\n| 1 | 登录 |"
    fake2 = [f"页眉Z\n{n + 1}\n第 {n + 1} 页 共 3 页\n{tbl}" for n in range(3)]
    out2 = clean_mod.clean_pdf(fake2)
    n_tbl = sum(1 for l in out2.split("\n") if l.startswith("|"))
    rec("S6", n_tbl == 9, f"表格行 9 行原样保留（实得 {n_tbl}）"
        + ("" if "页眉Z" not in out2 else "；⚠️ 页眉没清掉"))

    for cid, ok, why in rows:
        print(f"[{'PASS' if ok else 'FAIL'}] {cid}  {why}")
    nfail = sum(1 for _, ok, _ in rows if not ok)
    print(f"\nPASS={len(rows) - nfail}/{len(rows)}  FAIL={nfail}")
    return 1 if nfail else 0


if __name__ == "__main__":
    raise SystemExit(main())

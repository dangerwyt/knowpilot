"""量化 PDF/文档解析里的**零信息噪声**（页眉 / 页码 / 目录点线）—— 将来做 `clean()` 的验收判据。

为什么要有它：`parsers.py` 目前没有清洗阶段，`extract_text()` 出来的一坨直接进切分器。
09-22 实测《冥晨点餐中台用户手册》87 块：**60% 的块含页眉串**（全库出现 72 次）、70 次页码串、
25% 的块含目录点线（`订单中心 ........`）。

⚠️ 关键认识：页眉**不是独立成块**，而是**拼接在正文里** —— 所以它不产生垃圾块（判据写成
「整块等于页眉」会恒假、以为一切正常），而是**稀释每一块的向量**：同一个串出现 72 次，
让这 52 块互相「抱团」，任何沾「点餐/中台」的 query 都会把它们一起拉上来。

用法：
  ./.venv/Scripts/python.exe playground/pdf_noise_check.py <doc_id 前缀> [--show]
  ./.venv/Scripts/python.exe playground/pdf_noise_check.py be1d4f37          # 只看数字
  ./.venv/Scripts/python.exe playground/pdf_noise_check.py be1d4f37 --show   # 附带噪声块的原文

将来 `clean()` 落地后的验收判据（三条，都要能 FAIL）：
  ① 含页眉串的块数占比 —— 目标 0%（现在是 60%）
  ② 页眉串在全库的出现次数 —— 目标 0 次（现在是 72）
  ③ 块总数不应**上升**（清洗若把内容切碎了，块数会涨）

⚠️ 噪声模式与文档强相关：这里的判据（「冥晨点餐中台系统」「第 N 页 共 M 页」）是照这份手册写的。
换文档要改 `NOISE_PATTERNS`，别直接套 —— 换文档后判据恒假会让你误以为"已经没有噪声了"。
"""
import argparse
import re
import sys

sys.path.insert(0, ".")
from app.core.config import settings  # noqa: E402
from pymilvus import MilvusClient  # noqa: E402

# 页眉：这一串在该手册里每个页面都出现 ⇒ 出现在 60% 的块里
HEADER_TOKEN = "冥晨点餐中台系统"
# 页码串「第 N 页 共 M 页」
RULER = re.compile(r"第\s*\d+\s*页\s*共\s*\d+\s*页")
# 目录点线：≥4 个连续点（中英文句点/间隔号都算）
DOTLEAD = re.compile(r"\.{4,}|。{4,}|·{4,}")
# 纯页码（整块只有一个数字）
PAGENO = re.compile(r"^\s*\d{1,3}\s*$")


def main() -> None:
    ap = argparse.ArgumentParser(description="量化解析噪声（页眉/页码/目录点线）")
    ap.add_argument("doc_prefix", help="文档 id 前缀（如 be1d4f37）")
    ap.add_argument("--show", action="store_true", help="打印噪声块的原文片段")
    args = ap.parse_args()

    c = MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token)
    c.load_collection(settings.milvus_collection)
    rows = c.query(collection_name=settings.milvus_collection, filter="",
                   output_fields=["content", "document_id", "seq"], limit=10000)
    rows = sorted([r for r in rows if r["document_id"].startswith(args.doc_prefix)],
                  key=lambda r: r["seq"])
    if not rows:
        print(f"没找到 doc_id 以 {args.doc_prefix} 开头的块")
        return

    n = len(rows)
    hdr_blocks = [r for r in rows if HEADER_TOKEN in r["content"]]
    hdr_total = sum(r["content"].count(HEADER_TOKEN) for r in rows)
    dot_blocks = [r for r in rows if DOTLEAD.search(r["content"])]
    ruler_total = sum(len(RULER.findall(r["content"])) for r in rows)
    pageno_blocks = [r for r in rows if PAGENO.match(r["content"])]

    print(f"文档 {args.doc_prefix}* 共 {n} 块\n")
    print(f"  ① 含页眉串「{HEADER_TOKEN}」   {len(hdr_blocks):>3} 块 = {len(hdr_blocks)/n:>4.0%}"
          f"   全库出现 {hdr_total} 次   (目标 0%)")
    print(f"  ② 页码串「第 N 页 共 M 页」     出现 {ruler_total} 次          (目标 0)")
    print(f"  ③ 含目录点线                  {len(dot_blocks):>3} 块 = {len(dot_blocks)/n:>4.0%}   (目标 0%)")
    print(f"  ④ 整块只有一个数字            {len(pageno_blocks):>3} 块")
    print(f"\n  ⚠️ 注意：`clean()` 落地后还要看**块总数不应上升** —— 现在是 {n} 块。")

    if args.show:
        print("\n── 含页眉的块（前 8 个）原文：")
        for r in hdr_blocks[:8]:
            print(f"  [{r['seq']:>3}] {r['content'][:88].replace(chr(10), ' / ')}")
        print("\n── 含目录点线的块（前 8 个）原文：")
        for r in dot_blocks[:8]:
            print(f"  [{r['seq']:>3}] {r['content'][:88].replace(chr(10), ' / ')}")


if __name__ == "__main__":
    main()

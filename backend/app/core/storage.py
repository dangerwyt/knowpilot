"""原始文件本地落盘（OCR 原料预留；未来换 OSS 只改这里）。"""
from pathlib import Path

STORAGE_DIR = Path(__file__).resolve().parents[2] / "storage"  # → backend/storage


def save_original(kb_id: str, doc_id: str, ext: str, raw: bytes) -> str:
    """写原始文件，返回相对路径（存 object_key）。失败不阻断上传：best-effort + print 留痕。"""
    rel = f"{kb_id}/{doc_id}.{ext}"
    path = STORAGE_DIR / rel
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    except Exception as e: # noinspection PyBroadException
        print(f"[storage] 原始文件落盘失败（忽略）: {e}")
        return ""
    return rel

def read_original(object_key: str) -> bytes:
    """读原始文件，空值/不存在都归一成 ValueError，由调用方落 failed。"""
    if not object_key:
        raise ValueError("原始文件缺失（上传时落盘失败），请重新上传该文档")

    path = STORAGE_DIR / object_key
    if not path.exists():
        raise ValueError(f"原始文件不存在（可能已被清理），请重新上传该文档: {object_key}")
    return path.read_bytes()


def delete_original(object_key: str | None) -> None:
    """按 object_key 删文件；文件删完后若所在目录空了，一起收掉。"""
    if not object_key:
        return
    path = STORAGE_DIR / object_key
    path.unlink(missing_ok=True)

    # 收掉空掉的父目录（storage/<kb_id>）。两个守卫：
    #   ① 父目录必须是 storage 的真子目录 —— 挡掉「object_key 只有一层时退到 storage 根」
    #   ② 只删空目录 —— 非空时 rmdir 抛 OSError，那是常态（同 KB 还有别的文档）
    parent = path.parent
    if STORAGE_DIR in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            pass
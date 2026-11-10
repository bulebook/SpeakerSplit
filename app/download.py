"""模型文件下载：带重试和断点续传，支持镜像前缀。"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from pathlib import Path

CHUNK = 1 << 20


def download(url: str, dst: Path, retries: int = 3, timeout: int = 60,
             on_progress=None) -> Path:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 0:
        return dst

    part = dst.with_suffix(dst.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            have = part.stat().st_size if part.exists() else 0
            req = urllib.request.Request(url, headers={"User-Agent": "SpeakerSplit/0.1"})
            if have:
                req.add_header("Range", f"bytes={have}-")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                mode = "ab" if have and resp.status == 206 else "wb"
                if mode == "wb":
                    have = 0
                total = resp.headers.get("Content-Length")
                total = int(total) + have if total else None
                with open(part, mode) as f:
                    done = have
                    last = 0.0
                    while True:
                        block = resp.read(CHUNK)
                        if not block:
                            break
                        f.write(block)
                        done += len(block)
                        now = time.time()
                        if now - last > 1:
                            last = now
                            if on_progress:
                                on_progress(done, total or 0)
                            pct = f"{done * 100 / total:5.1f}%" if total else ""
                            print(f"\r  下载 {dst.name} {done / 1048576:.1f}MB {pct}", end="", flush=True)
            print()
            if total and part.stat().st_size != total:
                raise IOError(f"大小不符，期望 {total}，实际 {part.stat().st_size}")
            part.replace(dst)
            return dst
        except (urllib.error.URLError, IOError, TimeoutError) as e:
            print(f"\n  下载失败({attempt}/{retries}): {e}")
            if attempt == retries:
                raise
            time.sleep(2 * attempt)
    return dst


def apply_mirror(url: str) -> str:
    """把 github.com 的下载链接换成镜像前缀（可通过环境变量关闭）。"""
    prefix = os.environ.get("GITHUB_PROXY", "").strip()
    if prefix and "github.com" in url:
        return prefix.rstrip("/") + "/" + url
    return url


def ensure_hf_endpoint() -> None:
    """直连 huggingface.co 不通时自动切镜像；用户显式设置则不覆盖。"""
    if os.environ.get("HF_ENDPOINT"):
        return
    try:
        urllib.request.urlopen("https://huggingface.co", timeout=3)
    except Exception:
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

"""torch 运行时目录：CPU 版随安装包分发，GPU 版首次使用前按需下载。

安装包里不再内置 CUDA 版 torch（约 3.4 GB 的 CUDA 库），而是：

    <程序目录>/runtimes/cpu/torch      ← 随包分发，任何机器都能跑
    <程序目录>/runtimes/gpu/torch      ← 检测到 N 卡后下载，解压到这里

启动时把可用的那个放到 sys.path 最前面，之后 `import torch` 走外部目录。
torch 在 PyInstaller 包里被排除，避免和外部目录两份打架。
"""
from __future__ import annotations

import shutil
import sys
import tarfile
from pathlib import Path

from .download import apply_mirror, download

# GPU 加速包（CUDA 版 torch + torchaudio），发布在 GitHub Release 上
GPU_RELEASE = ("https://github.com/bulebook/SpeakerSplit/releases/download/"
               "v2.2.1/SpeakerSplit-2.2.1-gpu-pack.tar.xz")
GPU_PARTS = 2           # 分卷数，打包时确定
GPU_SIZE_TEXT = "约 2 GB"

_ACTIVE = ""


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def runtimes_root() -> Path:
    return base_dir() / "runtimes"


def runtime_dir(name: str) -> Path:
    return runtimes_root() / name


def installed(name: str) -> bool:
    return (runtime_dir(name) / "torch" / "__init__.py").is_file()


def active() -> str:
    """activate() 选中的运行时：'gpu' / 'cpu' / ''（都没装）。"""
    return _ACTIVE


def gpu_ready() -> bool:
    return installed("gpu")


def activate() -> str:
    """把可用的 torch 放到 sys.path 最前面；必须在任何 torch 导入之前调用。"""
    global _ACTIVE
    for name in ("gpu", "cpu"):
        path = runtime_dir(name)
        if installed(name):
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
            _ACTIVE = name
            return name
    _ACTIVE = ""
    return ""


def has_nvidia_gpu() -> bool:
    """不导入 torch，直接问 nvidia-smi。"""
    import subprocess

    try:
        out = subprocess.run(["nvidia-smi", "-L"], capture_output=True,
                             text=True, timeout=8,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.returncode == 0 and "GPU" in out.stdout
    except Exception:
        return False


def gpu_part_names() -> list[str]:
    return [f"{GPU_RELEASE.rsplit('/', 1)[-1]}.part{i:02d}" for i in range(GPU_PARTS)]


def download_gpu(on_progress=None, on_stage=None) -> Path:
    """下载并解压 GPU 包，返回运行时目录。中断后再次调用会续传。"""
    name = GPU_RELEASE.rsplit("/", 1)[-1]
    tmp = runtimes_root() / "_gpu_tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    total_parts = len(gpu_part_names())
    for i in range(total_parts):
        if on_stage:
            on_stage(f"下载 GPU 包 {i + 1}/{total_parts}")
        url = apply_mirror(f"{GPU_RELEASE}.part{i:02d}")
        download(url, tmp / f"{name}.part{i:02d}",
                 on_progress=lambda done, total, i=i: on_progress and on_progress(
                     i / total_parts + (done / max(total, 1)) / total_parts))

    tar_path = tmp / name
    if not tar_path.exists():
        if on_stage:
            on_stage("合并分卷")
        with open(tar_path, "wb") as out:
            for i in range(total_parts):
                with open(tmp / f"{name}.part{i:02d}", "rb") as src:
                    shutil.copyfileobj(src, out, 8 << 20)

    if on_stage:
        on_stage("解压 GPU 包（约 3.5 GB，需要一两分钟）")
    target = runtime_dir("gpu")
    tmp_target = runtimes_root() / "_gpu_unpack"
    if tmp_target.exists():
        shutil.rmtree(tmp_target, ignore_errors=True)
    tmp_target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:*") as tf:
        tf.extractall(tmp_target)          # nosec - 自家发布的包
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    tmp_target.replace(target)
    shutil.rmtree(tmp, ignore_errors=True)
    if on_progress:
        on_progress(1.0)
    return target

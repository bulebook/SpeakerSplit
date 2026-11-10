"""模型目录与下载清单。"""
from __future__ import annotations

import os
import sys
import tarfile
from pathlib import Path

from .download import apply_mirror, download


def _default_root() -> Path:
    if getattr(sys, "frozen", False):
        # 打包后放在 exe 同级的 models 目录，方便用户自己拷贝或清理
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = _default_root()

# 模型放程序目录下的 models/，也可用环境变量指定
MODELS_DIR = Path(os.environ.get("SPEAKERSPLIT_MODELS", ROOT / "models"))

SEG_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")


def _extract_tar_bz2(archive: Path, target_dir: Path) -> None:
    with tarfile.open(archive, "r:bz2") as tf:
        tf.extractall(target_dir)


def ensure_sherpa_models(spk_key: str | None = None) -> tuple[Path, Path]:
    """返回 (分割模型, 声纹嵌入模型) 的本地路径；嵌入模型按 spk_key 选择。"""
    seg_dir = MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0"
    seg_model = seg_dir / "model.onnx"
    if not seg_model.exists():
        archive = MODELS_DIR / "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
        download(apply_mirror(SEG_URL), archive)
        _extract_tar_bz2(archive, MODELS_DIR)
        archive.unlink(missing_ok=True)
        if not seg_model.exists():
            raise RuntimeError(f"分割模型解压后未找到 {seg_model}")

    from . import speaker_models as spk

    model = spk.BY_KEY.get(spk_key or "") or spk.BY_KEY[spk.DEFAULT_KEY]
    emb_model = spk.local_path(model)
    if not spk.is_downloaded(model):
        spk.download_model(model)
    return seg_model, emb_model

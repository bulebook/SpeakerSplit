"""人声分离模型清单：体积、效果、资源占用，以及下载与状态判断。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

RELEASE = ("https://github.com/nomadkaraoke/python-audio-separator"
           "/releases/download/model-configs")


@dataclass(frozen=True)
class SepModel:
    key: str
    name: str
    filename: str
    size_mb: int
    sdr: float
    seconds_30s: float
    vram_gb: float
    ram_gb: float
    kind: str                      # roformer / mdx23c / mdx / demucs
    hf: tuple[str, str] | None = None
    yaml: str | None = None
    note: str = ""

    @property
    def size_text(self) -> str:
        return f"{self.size_mb} MB"

    @property
    def vram_text(self) -> str:
        return "CPU（约 3G 内存）" if self.vram_gb < 0.2 else f"{self.vram_gb:.1f} GB 显存"

    @property
    def speed_text(self) -> str:
        return f"30 秒音频约 {self.seconds_30s:.0f} 秒"


MODELS: list[SepModel] = [
    SepModel("melband_kim", "MelBand RoFormer（Kim）",
             "vocals_mel_band_roformer.ckpt", 871, 12.60, 4.7, 1.97, 1.21,
             "roformer", ("KimberleyJSN/melbandroformer", "MelBandRoformer.ckpt"),
             note="效果最好，速度也最快"),
    SepModel("bs_roformer", "BS-RoFormer 317",
             "model_bs_roformer_ep_317_sdr_12.9755.ckpt", 610, 11.77, 21.1, 1.58, 1.02,
             "roformer", ("1Mohammad/model_bs_roformer",
                          "model_bs_roformer_ep_317_sdr_12.9755.ckpt")),
    SepModel("mdx23c", "MDX23C-InstVoc HQ",
             "MDX23C-8KFFT-InstVoc_HQ.ckpt", 428, 10.56, 15.5, 1.57, 1.38,
             "mdx23c", None, "model_2_stem_full_band_8k.yaml",
             note="UVR5 常用模型"),
    SepModel("main406", "UVR-MDX-NET Main406",
             "UVR-MDX-NET_Main_406.onnx", 64, 10.44, 19.5, 0.0, 2.96,
             "mdx", ("Eddycrack864/UVR5-MDX-NET-VIP-MODELS", "UVR-MDX-NET_Main_406.onnx")),
    SepModel("kimvocal2", "Kim Vocal 2",
             "Kim_Vocal_2.onnx", 64, 10.18, 18.9, 0.0, 2.96,
             "mdx", ("LeeAeron/uvr_mdx-net", "Kim_Vocal_2.onnx")),
    SepModel("vocft", "UVR-MDX-NET Voc FT",
             "UVR-MDX-NET_Voc_FT.onnx", 64, 10.15, 19.0, 0.0, 2.96,
             "mdx", ("LeeAeron/uvr_mdx-net", "UVR-MDX-NET-Voc_FT.onnx")),
    SepModel("demucs", "Demucs htdemucs（最快）",
             "955717e8-8726e21a.th", 81, 8.9, 1.3, 0.54, 0.85,
             "demucs", note="速度最快，质量一般"),
]

BY_KEY = {m.key: m for m in MODELS}
DEFAULT_KEY = "melband_kim"


def uvr_dir() -> Path:
    from .modelstore import MODELS_DIR

    path = MODELS_DIR / "uvr"
    path.mkdir(parents=True, exist_ok=True)
    return path


def local_path(model: SepModel) -> Path:
    if model.kind == "demucs":
        cache = Path(os.path.expanduser("~")) / ".cache" / "torch" / "hub" / "checkpoints"
        return cache / model.filename
    return uvr_dir() / model.filename


def is_downloaded(model: SepModel) -> bool:
    path = local_path(model)
    if not path.exists():
        return False
    if model.kind == "demucs":
        return path.stat().st_size > 10_000_000
    return path.stat().st_size > model.size_mb * 1024 * 1024 * 0.85


def download_model(model: SepModel, on_progress=None) -> Path:
    """下载模型（Demucs 由 torch 自己管理，这里直接返回）。"""
    from .download import download

    path = local_path(model)
    if model.kind == "demucs":
        return path

    if not is_downloaded(model):
        if path.exists():
            path.unlink()

        if model.hf:
            repo, remote = model.hf
            base = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com").rstrip("/")
            url = f"{base}/{repo}/resolve/main/{remote}"
        else:
            prefix = os.environ.get("GITHUB_PROXY", "https://gh-proxy.com/").strip()
            url = f"{prefix.rstrip('/')}/{RELEASE}/{model.filename}" if prefix else \
                f"{RELEASE}/{model.filename}"

        download(url, path, on_progress=on_progress)

    if model.yaml:
        yaml_path = uvr_dir() / model.yaml
        if not yaml_path.exists():
            prefix = os.environ.get("GITHUB_PROXY", "https://gh-proxy.com/").strip()
            yurl = f"{prefix.rstrip('/')}/{RELEASE}/{model.yaml}" if prefix else \
                f"{RELEASE}/{model.yaml}"
            download(yurl, yaml_path)
    return path


def ensure_downloaded(model: SepModel) -> Path:
    if not is_downloaded(model):
        raise RuntimeError(
            f"模型「{model.name}」还没下载，请在界面里点「切换模型」下载后再用")
    return local_path(model)

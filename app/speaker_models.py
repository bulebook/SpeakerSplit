"""声纹嵌入模型清单：全部来自 sherpa-onnx 官方发布，可直接替换。

分割模型仍固定使用 sherpa-onnx 的 pyannote segmentation 3.0；
pyannote.audio / Sortformer / DiariZen 属于其他运行时，暂未集成。
"""
from __future__ import annotations

from dataclasses import dataclass

RELEASE = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "speaker-recongition-models")


@dataclass(frozen=True)
class SpkModel:
    key: str
    name: str
    filename: str
    size_mb: float
    tags: tuple[str, ...] = ()
    note: str = ""

    @property
    def size_text(self) -> str:
        return f"{self.size_mb:.0f} MB"


MODELS: list[SpkModel] = [
    SpkModel("campplus_zh", "3D-Speaker CAM++ 中文",
             "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx", 27.0,
             ("推荐",), "默认，体积小、中文对话稳"),
    SpkModel("campplus_zh_en", "3D-Speaker CAM++ 中英",
             "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx", 27.0,
             ("中英混合",), "中英混说素材更稳，体积同上"),
    SpkModel("eres2netv2_zh", "3D-Speaker ERes2NetV2 中文",
             "3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx", 68.1,
             ("高精度",), "相似音色区分更好，比 CAM++ 慢"),
    SpkModel("eres2net_large_zh", "3D-Speaker ERes2Net-Large 中文",
             "3dspeaker_speech_eres2net_large_sv_zh-cn_3dspeaker_16k.onnx", 110.7,
             ("最高精度",), "最慢，嘈杂、多人长会议再考虑"),
    SpkModel("wespeaker_zh_r34", "WeSpeaker CN-Celeb ResNet34 中文",
             "wespeaker_zh_cnceleb_resnet34_LM.onnx", 25.3,
             ("轻量",), "体积最小，速度接近 CAM++"),
    SpkModel("wespeaker_en_r293", "WeSpeaker VoxCeleb ResNet293",
             "wespeaker_en_voxceleb_resnet293_LM.onnx", 109.0,
             ("英文首选",), "英文素材为主时效果更好"),
]

BY_KEY = {m.key: m for m in MODELS}
DEFAULT_KEY = "campplus_zh"


def local_path(model: SpkModel):
    from .modelstore import MODELS_DIR

    return MODELS_DIR / model.filename


def is_downloaded(model: SpkModel) -> bool:
    path = local_path(model)
    return path.exists() and path.stat().st_size > model.size_mb * 1024 * 1024 * 0.85


def download_model(model: SpkModel, on_progress=None):
    import os

    from .download import download

    path = local_path(model)
    if is_downloaded(model):
        return path

    prefix = os.environ.get("GITHUB_PROXY", "https://gh-proxy.com/").strip()
    url = f"{prefix.rstrip('/')}/{RELEASE}/{model.filename}" if prefix else \
        f"{RELEASE}/{model.filename}"
    download(url, path, on_progress=on_progress)
    return path

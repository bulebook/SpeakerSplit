"""语音转写：faster-whisper，输出词级时间戳。"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_models: dict[tuple[str, str, str], object] = {}
_dll_ready = False


def _prepare_cuda_dlls() -> None:
    """ctranslate2 需要 cuBLAS / cuDNN，torch 的 wheel 里已自带。"""
    global _dll_ready
    if _dll_ready:
        return
    _dll_ready = True
    if sys.platform != "win32":
        return
    try:
        import torch

        lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.isdir(lib):
            os.add_dll_directory(lib)
            os.environ["PATH"] = lib + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str


def _pick_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def _pick_compute_type(device: str, compute_type: str | None) -> str:
    if compute_type:
        return compute_type
    return "float16" if device == "cuda" else "int8"


def _is_gpu_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(k in msg for k in ("cublas", "cudnn", "cuda", "gpu"))


def load_model(name: str, device: str = "auto", compute_type: str | None = None):
    _prepare_cuda_dlls()
    from faster_whisper import WhisperModel

    device = _pick_device(device)
    compute_type = _pick_compute_type(device, compute_type)
    key = (name, device, compute_type)
    if key not in _models:
        try:
            _models[key] = WhisperModel(name, device=device, compute_type=compute_type)
        except Exception:
            if device != "cuda":
                raise
            key = (name, "cpu", "int8")
            _models[key] = WhisperModel(name, device="cpu", compute_type="int8")
    return _models[key]


def _run(audio: Path, model_name: str, device: str, compute_type: str | None,
         language: str | None, on_progress) -> dict:
    model = load_model(model_name, device, compute_type)
    segments_iter, info = model.transcribe(
        str(audio),
        language=language,
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        condition_on_previous_text=False,
    )

    words: list[Word] = []
    segments: list[Segment] = []
    for seg in segments_iter:
        segments.append(Segment(seg.start, seg.end, seg.text.strip()))
        for w in (seg.words or []):
            text = w.word.strip()
            if text:
                words.append(Word(w.start, w.end, text))
        if on_progress:
            on_progress(seg.end, info.duration)

    return {
        "language": info.language,
        "language_probability": info.language_probability,
        "words": words,
        "segments": segments,
    }


def transcribe(audio: Path, model_name: str = "large-v3-turbo",
               device: str = "auto", compute_type: str | None = None,
               language: str | None = None,
               on_progress: Callable[[float, float], None] | None = None) -> dict:
    """返回 {language, words, segments}。显卡相关运行库缺失时自动回退 CPU。"""
    try:
        return _run(audio, model_name, device, compute_type, language, on_progress)
    except RuntimeError as exc:
        if _is_gpu_error(exc) and _pick_device(device) == "cuda":
            print("显卡推理不可用，改用 CPU 转写", flush=True)
            _models.clear()
            return _run(audio, model_name, "cpu", "int8", language, on_progress)
        raise

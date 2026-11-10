"""混音渲染：numpy 相加，支持增益、淡入淡出、静音/独奏、防削波。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf

from .edit_model import EditProject


@lru_cache(maxsize=24)
def _load(path: str, sr: int) -> np.ndarray:
    data, fsr = sf.read(path, dtype="float32", always_2d=True)
    if fsr != sr:
        try:
            import soxr
            data = soxr.resample(data, fsr, sr)
        except Exception:
            n = int(len(data) * sr / fsr)
            idx = np.linspace(0, len(data) - 1, n)
            data = np.stack([np.interp(idx, np.arange(len(data)), data[:, ch])
                             for ch in range(data.shape[1])], axis=1).astype(np.float32)
    if data.shape[1] == 1:
        data = np.repeat(data, 2, axis=1)
    elif data.shape[1] > 2:
        data = data[:, :2]
    return data


def render(project: EditProject, a: float = 0.0, b: float | None = None,
           progress=None) -> np.ndarray:
    sr = project.sr
    end = project.duration if b is None else b
    total = max(1, int(round((end - a) * sr)))
    out = np.zeros((total + sr // 2, 2), dtype=np.float32)

    solo = any(t.solo for t in project.tracks)
    active = [t for t in project.tracks if not t.mute and (not solo or t.solo)]
    for ti, track in enumerate(active):
        gain = 10 ** (track.gain_db / 20.0)
        for clip in track.clips:
            s, e = max(clip.start, a), min(clip.end, end)
            if e <= s:
                continue
            src = _load(clip.source, sr)
            i0 = int(round((clip.src_in + s - clip.start) * sr))
            n = int(round((e - s) * sr))
            seg = src[i0:i0 + n]
            if len(seg) == 0:
                continue
            n = len(seg)
            env = np.ones(n, dtype=np.float32)
            fi = min(n, int(round(clip.fade_in * sr)))
            fo = min(n, int(round(clip.fade_out * sr)))
            if fi > 0:
                env[:fi] *= np.linspace(0.0, 1.0, fi, dtype=np.float32)
            if fo > 0:
                env[-fo:] *= np.linspace(1.0, 0.0, fo, dtype=np.float32)
            o = int(round((s - a) * sr))
            out[o:o + n] += seg * (env * gain * 10 ** (clip.gain_db / 20.0))[:, None]
        if progress:
            progress((ti + 1) / max(1, len(active)))

    out = out[:total]
    peak = float(np.abs(out).max()) if total else 0.0
    if peak > 0.99:
        out *= 0.99 / peak
    return out


def export_wav(project: EditProject, path: Path, progress=None) -> Path:
    audio = render(project, 0.0, None, progress=progress)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio, project.sr, subtype="PCM_16")
    return path

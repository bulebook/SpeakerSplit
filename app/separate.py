"""人声分离：按模型清单里的 key 选择 Demucs / RoFormer / MDX。"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from . import separation_models as sm

DEMUCS_MODEL = "htdemucs"
_PROGRESS = re.compile(r"(\d+)%")


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _run_demucs(audio: Path, out_dir: Path, model: str, device: str,
                on_progress=None) -> Path:
    cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals",
           "-n", model, "-o", str(out_dir), "-d", device, str(audio)]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        tail.append(line)
        tail = tail[-40:]
        if on_progress and "%" in line:
            m = _PROGRESS.search(line)
            if m:
                on_progress(int(m.group(1)))
    code = proc.wait()
    if code != 0:
        raise RuntimeError("Demucs 分离失败:\n" + "\n".join(tail))

    vocals = out_dir / model / audio.stem / "vocals.wav"
    if not vocals.exists():
        raise RuntimeError(f"未找到 Demucs 输出: {vocals}")
    return vocals


def separate_vocals(audio: Path, out_dir: Path, model_key: str = sm.DEFAULT_KEY,
                    device: str = "auto", on_progress=None) -> Path:
    """返回人声轨 WAV 路径。"""
    audio = Path(audio)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = sm.BY_KEY.get(model_key, sm.BY_KEY[sm.DEFAULT_KEY])

    if spec.kind == "demucs":
        resolved = _resolve_device(device)
        if getattr(sys, "frozen", False):
            return _run_demucs_inproc(audio, out_dir, resolved, on_progress)
        try:
            return _run_demucs(audio, out_dir / "demucs", DEMUCS_MODEL, resolved,
                               on_progress)
        except RuntimeError:
            if resolved != "cuda":
                raise
            print("显卡处理失败，改用 CPU 重试", flush=True)
            return _run_demucs(audio, out_dir / "demucs", DEMUCS_MODEL, "cpu",
                               on_progress)

    return _separate_with_uvr(audio, out_dir, spec, on_progress)


def _run_demucs_inproc(audio: Path, out_dir: Path, device: str,
                       on_progress=None) -> Path:
    """打包成 exe 后没有 python 解释器可用，直接在当前进程里跑 Demucs。"""
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    out_dir.mkdir(parents=True, exist_ok=True)
    model = get_model(DEMUCS_MODEL).to(device)

    data, sr = sf.read(str(audio), dtype="float32", always_2d=True)
    wav = torch.from_numpy(np.ascontiguousarray(data.T))
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    if sr != model.samplerate:
        import torchaudio

        wav = torchaudio.functional.resample(wav, sr, model.samplerate)

    if on_progress:
        on_progress(30)
    with torch.no_grad():
        sources = apply_model(model, wav[None].to(device), device=device,
                              progress=False)[0]
    vocals = sources[model.sources.index("vocals")].cpu().numpy().T
    out = out_dir / "vocals.wav"
    sf.write(str(out), vocals, model.samplerate, subtype="PCM_16")
    if on_progress:
        on_progress(100)
    return out


def _separate_with_uvr(audio: Path, out_dir: Path, spec: sm.SepModel,
                       on_progress=None) -> Path:
    from audio_separator.separator import Separator

    model_dir = sm.uvr_dir()
    sm.download_model(spec)          # 已下载时只补缺失的 yaml
    sm.ensure_downloaded(spec)

    sep = Separator(model_file_dir=str(model_dir), output_dir=str(out_dir),
                    output_format="WAV", output_single_stem="Vocals")
    sep.load_model(model_filename=spec.filename)
    if on_progress:
        on_progress(50)
    outputs = sep.separate(str(audio))
    if on_progress:
        on_progress(100)
    if not outputs:
        raise RuntimeError(f"{spec.name} 没有任何输出")

    path = Path(outputs[0])
    if not path.is_absolute():
        path = out_dir / path.name
    if not path.exists():
        raise RuntimeError(f"未找到 {spec.name} 输出: {path}")
    return path

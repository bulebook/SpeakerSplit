"""FFmpeg 媒体探测与音轨提取。"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


class MediaError(RuntimeError):
    pass


@dataclass
class AudioStream:
    ordinal: int            # 第几条音频，从 0 开始
    codec: str
    channels: int
    sample_rate: int
    language: str | None = None
    title: str | None = None

    def describe(self) -> str:
        bits = [f"音轨{self.ordinal + 1}", self.codec, f"{self.channels}ch", f"{self.sample_rate}Hz"]
        if self.language:
            bits.append(self.language)
        if self.title:
            bits.append(self.title)
        return " / ".join(bits)


@dataclass
class MediaInfo:
    path: Path
    duration: float
    has_video: bool
    audio_streams: list[AudioStream] = field(default_factory=list)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise MediaError(f"命令失败: {' '.join(cmd)}\n{proc.stderr.strip()[-2000:]}")
    return proc


def probe(path: Path) -> MediaInfo:
    path = Path(path)
    if not path.exists():
        raise MediaError(f"文件不存在: {path}")
    out = _run([FFPROBE, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path)]).stdout
    data = json.loads(out)

    duration = float(data.get("format", {}).get("duration") or 0.0)
    has_video = False
    streams: list[AudioStream] = []
    ordinal = 0
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic"):
            has_video = True
        if s.get("codec_type") != "audio":
            continue
        tags = s.get("tags", {}) or {}
        streams.append(AudioStream(
            ordinal=ordinal,
            codec=s.get("codec_name", "?"),
            channels=int(s.get("channels") or 0),
            sample_rate=int(s.get("sample_rate") or 0),
            language=tags.get("language"),
            title=tags.get("title"),
        ))
        ordinal += 1

    if not streams:
        raise MediaError(f"文件里没有音轨: {path}")
    return MediaInfo(path=path, duration=duration, has_video=has_video, audio_streams=streams)


def extract_audio(src: Path, ordinal: int, dst: Path,
                  sample_rate: int | None = None, channels: int | None = None) -> Path:
    """从媒体中提取指定音轨为 16-bit WAV。"""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-y", "-v", "error", "-i", str(src), "-map", f"0:a:{ordinal}", "-vn"]
    if sample_rate:
        cmd += ["-ar", str(sample_rate)]
    if channels:
        cmd += ["-ac", str(channels)]
    cmd += ["-c:a", "pcm_s16le", str(dst)]
    _run(cmd)
    return dst

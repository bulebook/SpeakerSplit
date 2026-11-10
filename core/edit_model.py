"""音频编辑数据模型：所有编辑只记录引用，不改原文件。"""
from __future__ import annotations

from dataclasses import dataclass, field


SEGMENT_MIME = "application/x-speakersplit-segment"


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex[:8]


@dataclass
class Clip:
    source: str
    src_in: float
    src_out: float
    start: float
    gain_db: float = 0.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    id: str = field(default_factory=_new_id)

    @property
    def dur(self) -> float:
        return max(0.0, self.src_out - self.src_in)

    @property
    def end(self) -> float:
        return self.start + self.dur


@dataclass
class Track:
    name: str
    color: str = "#007AFF"
    clips: list[Clip] = field(default_factory=list)
    mute: bool = False
    solo: bool = False
    gain_db: float = 0.0


@dataclass
class EditProject:
    tracks: list[Track] = field(default_factory=list)
    sr: int = 48000

    @property
    def duration(self) -> float:
        return max((c.end for t in self.tracks for c in t.clips), default=0.0)

    def track(self, index: int) -> Track:
        return self.tracks[index]


def from_segments(source: str, segments: list[dict], names: list[str],
                  colors: list[str]) -> EditProject:
    """把识别结果转成编辑工程：每个说话人一条轨道，片段按原时间摆放。"""
    proj = EditProject()
    for i, name in enumerate(names):
        proj.tracks.append(Track(name=name, color=colors[i % len(colors)]))
    index = {name: i for i, name in enumerate(names)}
    for seg in segments:
        if seg.get("excluded"):
            continue
        idx = index.get(seg.get("speaker"))
        if idx is None:
            continue
        start, end = float(seg["start"]), float(seg["end"])
        if end - start < 0.02:
            continue
        proj.tracks[idx].clips.append(
            Clip(source=source, src_in=start, src_out=end, start=start))
    for track in proj.tracks:
        track.clips.sort(key=lambda c: c.start)
    return proj

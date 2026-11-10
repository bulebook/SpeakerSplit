"""编辑操作：叠加、插入、切开、删除。"""
from __future__ import annotations

import copy

from .edit_model import Clip, EditProject, Track


def split(track: Track, t: float):
    """在 t 秒处切开跨过该点的片段。"""
    for clip in list(track.clips):
        if clip.start + 1e-6 < t < clip.end - 1e-6:
            right = copy.copy(clip)
            import uuid
            right.id = uuid.uuid4().hex[:8]
            cut = clip.src_in + (t - clip.start)
            clip.src_out = cut
            right.src_in = cut
            right.start = t
            track.clips.append(right)


def split_all(proj: EditProject, t: float):
    for track in proj.tracks:
        split(track, t)


def overlay(proj: EditProject, track_index: int, clip: Clip):
    """叠加：放在 clip.start，与其他轨道同时发声。"""
    proj.tracks[track_index].clips.append(clip)


def insert(proj: EditProject, track_index: int, clip: Clip,
           ripple_all: bool = True):
    """插入：在 clip.start 处切开，后面的内容整体后移。"""
    at = clip.start
    dur = clip.dur
    targets = proj.tracks if ripple_all else [proj.tracks[track_index]]
    for track in targets:
        split(track, at)
        for c in track.clips:
            if c.start >= at - 1e-6:
                c.start += dur
    proj.tracks[track_index].clips.append(clip)


def delete_range(track: Track, a: float, b: float, ripple: bool = False):
    split(track, a)
    split(track, b)
    track.clips = [
        c for c in track.clips
        if not (c.start >= a - 1e-6 and c.end <= b + 1e-6)]
    if ripple:
        span = max(0.0, b - a)
        for c in track.clips:
            if c.start >= b - 1e-6:
                c.start -= span


def delete_clip(track: Track, clip_id: str):
    track.clips = [c for c in track.clips if c.id != clip_id]


def duplicate(proj: EditProject, ids: set[str]) -> set[str]:
    """把选中的片段各复制一份，接在原片段后面；返回新片段 id。"""
    import uuid

    new_ids = set()
    for track in proj.tracks:
        for clip in list(track.clips):
            if clip.id not in ids:
                continue
            new = copy.copy(clip)
            new.id = uuid.uuid4().hex[:8]
            new.start = clip.end
            track.clips.append(new)
            new_ids.add(new.id)
    for track in proj.tracks:
        track.clips.sort(key=lambda c: c.start)
    return new_ids


def delete_clips(proj: EditProject, ids: set[str]):
    for track in proj.tracks:
        track.clips = [c for c in track.clips if c.id not in ids]


def ripple_delete(proj: EditProject, ids: set[str]):
    """删除选中片段，并把同一轨道后面的片段前移对应时长。"""
    for track in proj.tracks:
        removed = sorted((c for c in track.clips if c.id in ids),
                         key=lambda c: c.start)
        if not removed:
            continue
        track.clips = [c for c in track.clips if c.id not in ids]
        for gone in removed:
            start, dur = gone.start, gone.dur
            for other in track.clips:
                if other.start >= start - 1e-6:
                    other.start = max(start, other.start - dur)


_COLORS = ["#007AFF", "#34C759", "#FF9500", "#AF52DE", "#FF2D55", "#5AC8FA"]


def new_track(proj: EditProject, name: str | None = None) -> Track:
    index = len(proj.tracks)
    return Track(name=name or f"轨道 {index + 1}",
                 color=_COLORS[index % len(_COLORS)])

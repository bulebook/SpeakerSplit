"""把词级时间戳和说话人区间对齐，合并成可检查的语句片段。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .diarize import Turn
from .transcribe import Word

# 同一说话人相邻词的最大间隔，超过则切成新片段
JOIN_GAP = 1.5
# 低于这个概率的词不参与判断（faster-whisper 没给词概率时忽略）
_ASCII_WORD = re.compile(r"^[A-Za-z0-9]")


@dataclass
class Utterance:
    start: float
    end: float
    speaker: int | None
    text: str
    words: list[Word] = field(default_factory=list)
    flag: str | None = None
    excluded: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _best_speaker(word: Word, turns: list[Turn]) -> int | None:
    best, best_score = None, 0.0
    center = (word.start + word.end) / 2
    for t in turns:
        score = _overlap(word.start, word.end, t.start, t.end)
        if score <= 0 and t.start <= center <= t.end:
            score = 1e-6
        if score > best_score:
            best, best_score = t.speaker, score
    return best


def _join_text(words: list[Word]) -> str:
    out = ""
    for w in words:
        t = w.text.strip()
        if not t:
            continue
        if out and (_ASCII_WORD.match(t) or _ASCII_WORD.match(out[-1])):
            out += " "
        out += t
    return out


def assign_speakers(words: list[Word], turns: list[Turn],
                    join_gap: float = JOIN_GAP) -> list[Utterance]:
    """逐词判定说话人，再按说话人连续性合并。"""
    if not words:
        return []
    if not turns:
        return [Utterance(words[0].start, words[-1].end, None, _join_text(words), list(words))]

    utterances: list[Utterance] = []
    current: Utterance | None = None

    for w in sorted(words, key=lambda x: x.start):
        spk = _best_speaker(w, turns)
        if (current is not None
                and current.speaker == spk
                and w.start - current.end <= join_gap):
            current.words.append(w)
            current.end = max(current.end, w.end)
            current.text = _join_text(current.words)
        else:
            current = Utterance(w.start, w.end, spk, w.text, [w])
            utterances.append(current)

    for u in utterances:
        u.text = _join_text(u.words)
    return utterances


def mark_overlaps(utterances: list[Utterance], turns: list[Turn],
                  min_overlap: float = 0.3) -> list[Utterance]:
    """标出多人同时说话的片段，只做提示，不自动删除。"""
    ranges: list[tuple[float, float]] = []
    for i, a in enumerate(turns):
        for b in turns[i + 1:]:
            if a.speaker == b.speaker:
                continue
            start, end = max(a.start, b.start), min(a.end, b.end)
            if end - start >= min_overlap:
                ranges.append((start, end))
    if not ranges:
        return utterances

    ranges.sort()
    merged = [list(ranges[0])]
    for start, end in ranges[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    for u in utterances:
        if u.flag:
            continue
        for start, end in merged:
            if min(u.end, end) - max(u.start, start) > 0.2:
                u.flag = "overlap"
                break
    return utterances


def speaker_names(utterances: list[Utterance]) -> dict[int | None, str]:
    """按首次出现顺序给说话人编号，未分配固定最后。"""
    mapping: dict[int | None, str] = {}
    n = 0
    for u in utterances:
        if u.speaker is None:
            continue
        if u.speaker not in mapping:
            n += 1
            mapping[u.speaker] = f"说话人{n:02d}"
    mapping[None] = "未分配"
    return mapping

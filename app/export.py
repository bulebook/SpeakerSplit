"""按说话人导出 WAV 和片段清单。"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .align import Utterance


def _hms(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _read_write(src: Path, dst: Path, spans: list[tuple[float, float]]) -> float:
    import soundfile as sf

    total = 0.0
    with sf.SoundFile(str(src)) as f:
        sr = f.samplerate
        channels = f.channels
        with sf.SoundFile(str(dst), "w", samplerate=sr, channels=channels,
                          subtype="PCM_16") as out:
            for start, end in spans:
                a = max(0, int(round(start * sr)))
                b = min(len(f), int(round(end * sr)))
                if b <= a:
                    continue
                f.seek(a)
                out.write(f.read(b - a))
                total += (b - a) / sr
    return total


def export_by_speaker(source_audio: Path, utterances: list[Utterance],
                      names: dict[int | None, str], out_root: Path) -> dict:
    """每个说话人一个子文件夹和一个同名 WAV；被排除的片段跳过。"""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    groups: dict[int | None, list[Utterance]] = defaultdict(list)
    for u in utterances:
        groups[u.speaker].append(u)

    summary = []
    for speaker, items in groups.items():
        kept = [u for u in items if not u.excluded and u.duration > 0.05]
        name = names.get(speaker, "未分配")
        if not kept:
            continue
        folder = out_root / name
        folder.mkdir(parents=True, exist_ok=True)
        wav_path = folder / f"{name}.wav"
        kept.sort(key=lambda u: u.start)
        seconds = _read_write(Path(source_audio), wav_path,
                              [(u.start, u.end) for u in kept])
        summary.append({"name": name, "file": str(wav_path),
                        "segments": len(kept), "seconds": seconds})

    csv_path = out_root / "片段清单.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["人物", "序号", "开始(秒)", "结束(秒)", "时长(秒)",
                    "开始时间", "结束时间", "文字", "备注"])
        for speaker, items in groups.items():
            name = names.get(speaker, "未分配")
            for i, u in enumerate(sorted(items, key=lambda x: x.start), 1):
                w.writerow([name, i, f"{u.start:.2f}", f"{u.end:.2f}",
                            f"{u.duration:.2f}", _hms(u.start), _hms(u.end), u.text,
                            "疑似重叠" if u.flag == "overlap" else ""])

    return {"speakers": summary, "csv": str(csv_path)}

"""命令行入口：在独立子进程里做说话人识别，避免占住界面线程的 GIL。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .diarize import diarize


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        return 2
    audio, seg_model, emb_model, out_path = argv[:4]
    kwargs = json.loads(argv[4]) if len(argv) > 4 else {}
    turns = diarize(Path(audio), Path(seg_model), Path(emb_model), **kwargs)
    Path(out_path).write_text(
        json.dumps([[t.start, t.end, t.speaker] for t in turns]),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

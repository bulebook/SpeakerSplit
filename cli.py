from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app import media
from app.download import ensure_hf_endpoint
from app.pipeline import run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="speakersplit",
        description="本地视频/音频说话人分离：去背景音乐 → 转写 → 按人导出 WAV")
    p.add_argument("source", type=Path, help="视频或音频文件")
    p.add_argument("-o", "--out", type=Path, help="输出目录（默认 <源文件名>_output）")
    p.add_argument("-t", "--track", type=int, default=0, help="音轨序号（默认 0）")
    p.add_argument("--list-tracks", action="store_true", help="只列出音轨后退出")
    p.add_argument("--no-clean", action="store_true", help="不做去背景音乐（快很多）")
    p.add_argument("--sep-model", default="melband_kim",
                   choices=["melband_kim", "bs_roformer", "mdx23c", "main406",
                            "kimvocal2", "vocft", "demucs"],
                   help="人声分离模型（默认 melband_kim，效果最好也最快）")
    p.add_argument("--model", default="large-v3-turbo",
                   help="faster-whisper 模型名或本地目录（默认 large-v3-turbo）")
    p.add_argument("--language", default=None, help="语言代码，如 zh / en；默认自动识别")
    p.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    p.add_argument("--threshold", type=float, default=0.9,
                   help="声纹聚类阈值，越大越容易合并成同一人（默认 0.9）")
    p.add_argument("--merge-sim", type=float, default=0.8,
                   help="同人合并的声纹相似度阈值，调大更保守（默认 0.8）")
    p.add_argument("--speakers", type=int, default=-1, help="已知人数时指定，-1 为自动")
    p.add_argument("--spk-model", default="campplus_zh",
                   choices=["campplus_zh", "campplus_zh_en", "eres2netv2_zh",
                            "eres2net_large_zh", "wespeaker_zh_r34",
                            "wespeaker_en_r293"],
                   help="声纹嵌入模型（默认 campplus_zh）")
    p.add_argument("--provider", default="cpu", choices=["cpu", "cuda"],
                   help="声纹模型推理后端（默认 cpu）")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    if not args.source.exists():
        print(f"文件不存在: {args.source}", file=sys.stderr)
        return 2

    info = media.probe(args.source)
    if args.list_tracks:
        print(f"{args.source.name}  时长 {info.duration:.1f}s  "
              f"{'视频' if info.has_video else '音频'}")
        for s in info.audio_streams:
            print(f"  [{s.ordinal}] {s.describe()}")
        return 0

    out = args.out or args.source.with_name(args.source.stem + "_output")
    print(f"输出目录: {out}")

    ensure_hf_endpoint()
    run(args.source, out,
        audio_track=args.track,
        clean=not args.no_clean,
        sep_model=args.sep_model,
        whisper_model=args.model,
        device=args.device,
        language=args.language,
        threshold=args.threshold,
        merge_sim=args.merge_sim,
        num_speakers=args.speakers,
        spk_model=args.spk_model,
        provider=args.provider)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

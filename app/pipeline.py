"""完整处理流水线。"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from . import align, diarize, export, media, separate, transcribe
from .modelstore import ensure_sherpa_models

ANALYSIS_RATE = 16000


def _log(msg: str) -> None:
    print(msg, flush=True)


def run(source: Path, out_dir: Path, *, audio_track: int = 0, clean: bool = True,
        sep_model: str = "melband_kim",
        whisper_model: str = "large-v3-turbo", device: str = "auto",
        language: str | None = None, threshold: float = 0.9,
        num_speakers: int = -1, provider: str = "cpu",
        min_speaker_sec: float = 3.0,
        merge_sim: float = 0.8,
        spk_model: str | None = None,
        on_stage: Callable[[str, str], None] | None = None,
        on_progress: Callable[[float], None] | None = None) -> dict:
    """on_stage(阶段键, 显示文字)，on_progress(0~1 当前阶段进度)。"""
    source = Path(source)
    out_dir = Path(out_dir)
    work = out_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    def stage(key: str, text: str) -> None:
        _log(text)
        if on_stage:
            on_stage(key, text)

    def progress(v: float) -> None:
        if on_progress:
            on_progress(max(0.0, min(1.0, v)))

    stage("probe", "读取素材信息")
    info = media.probe(source)
    if audio_track < 0 or audio_track >= len(info.audio_streams):
        raise ValueError(f"音轨序号 {audio_track} 无效，共 {len(info.audio_streams)} 条")
    _log(f"      时长 {info.duration:.1f}s，{'视频' if info.has_video else '音频'}，"
         f"{len(info.audio_streams)} 条音轨")

    stage("extract", "提取音轨")
    original = media.extract_audio(source, audio_track, work / "original.wav")

    if clean:
        stage("separate", "分离人声，去除背景音乐")
        export_source = separate.separate_vocals(
            original, work / "demucs", model_key=sep_model, device=device,
            on_progress=lambda p: (print(f"\r      进度 {p:3d}%", end="", flush=True),
                                   progress(p / 100.0)))
        print()
    else:
        export_source = original

    stage("transcribe", "转写文字")
    analysis = media.extract_audio(export_source, 0, work / "analysis16k.wav",
                                   sample_rate=ANALYSIS_RATE, channels=1)
    asr = transcribe.transcribe(
        analysis, model_name=whisper_model, device=device, language=language,
        on_progress=lambda done, total: (print(
            f"\r      转写 {done:7.1f}/{total:7.1f}s ({done * 100 / max(total, 1e-6):5.1f}%)",
            end="", flush=True), progress(done / max(total, 1e-6))))
    print()
    _log(f"      语言 {asr['language']}（置信度 {asr['language_probability']:.2f}），"
         f"{len(asr['words'])} 个词")

    stage("diarize", "识别说话人")
    seg_model, emb_model = ensure_sherpa_models(spk_model)
    kwargs = dict(threshold=threshold, num_speakers=num_speakers,
                  provider=provider, min_duration_on=0.5, merge_sim=merge_sim)
    try:
        turns = diarize.diarize_subprocess(analysis, seg_model, emb_model, **kwargs)
    except Exception as exc:
        _log(f"      子进程识别失败，改用当前进程: {exc}")
        turns = diarize.diarize(analysis, seg_model, emb_model, **kwargs)
    _log(f"      {len(turns)} 个说话人区间")

    # 说话太少的簇多半是短噪声或被误分的音效，归入未分配，避免出现大量"一人一句"
    total_sec: dict[int, float] = {}
    for t in turns:
        total_sec[t.speaker] = total_sec.get(t.speaker, 0.0) + (t.end - t.start)
    kept = {spk for spk, sec in total_sec.items() if sec >= min_speaker_sec}
    if len(kept) < len(total_sec):
        _log(f"      过滤掉 {len(total_sec) - len(kept)} 个说话过少的簇（<{min_speaker_sec:.0f}s）")
    turns = [t for t in turns if t.speaker in kept]

    utterances = align.assign_speakers(asr["words"], turns)
    utterances = align.mark_overlaps(utterances, turns)
    overlap_n = sum(1 for u in utterances if u.flag == "overlap")
    if overlap_n:
        _log(f"      {overlap_n} 个片段疑似多人重叠，已标记待人工确认")
    names = align.speaker_names(utterances)
    used = {u.speaker for u in utterances}
    _log(f"      判定 {len([s for s in used if s is not None])} 位说话人，"
         f"{len(utterances)} 个片段")

    stage("export", "按人导出")
    result = export.export_by_speaker(export_source, utterances, names, out_dir)
    for s in result["speakers"]:
        _log(f"      {s['name']}: {s['segments']} 段 / {s['seconds']:.1f}s -> {s['file']}")
    _log(f"      清单: {result['csv']}")

    project = {
        "version": 1,
        "source": str(source),
        "duration": info.duration,
        "audio_track": audio_track,
        "cleaned": clean,
        "export_source": str(export_source),
        "analysis_audio": str(analysis),
        "language": asr["language"],
        "speakers": [{"id": k, "name": v} for k, v in names.items()],
        "segments": [
            {"start": round(u.start, 3), "end": round(u.end, 3),
             "speaker": names.get(u.speaker, "未分配"), "text": u.text,
             "flag": u.flag}
            for u in utterances
        ],
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    project_path = out_dir / "project.json"
    project_path.write_text(json.dumps(project, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    stage("done", "完成")
    _log(f"完成，用时 {time.time() - t0:.1f}s；项目文件 {project_path}")
    return project

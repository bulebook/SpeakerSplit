"""说话人分离：sherpa-onnx（pyannote 分割 + 声纹嵌入）+ 按声纹二次合并。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16000
MIN_TURN_SEC = 0.25


@dataclass
class Turn:
    start: float
    end: float
    speaker: int


def load_audio_16k(path: Path):
    import numpy as np
    import soundfile as sf

    samples, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if sr != SAMPLE_RATE:
        raise ValueError(f"声纹分离需要 16kHz 音频，实际 {sr}Hz: {path}")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    return np.ascontiguousarray(samples, dtype=np.float32)


def _extract_embeddings(samples, turns, emb_model: Path, provider: str):
    """给每个说话片段单独提一次声纹向量，便于自己重新聚类。"""
    import numpy as np
    import sherpa_onnx

    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(emb_model), provider=provider))

    min_len = int(MIN_TURN_SEC * SAMPLE_RATE)
    vectors: list = []
    for turn in turns:
        a = max(0, int(turn.start * SAMPLE_RATE))
        b = min(len(samples), int(turn.end * SAMPLE_RATE))
        if b - a < min_len:
            vectors.append(None)
            continue
        stream = extractor.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples[a:b])
        stream.input_finished()
        if not extractor.is_ready(stream):
            vectors.append(None)
            continue
        vec = np.asarray(extractor.compute(stream), dtype="float32")
        norm = float(np.linalg.norm(vec))
        vectors.append(vec / norm if norm > 0 else None)
    return vectors


def _merge_similar(turns: list[Turn], vectors, sim_threshold: float,
                   min_turn: float = 1.0) -> list[Turn]:
    """用每个说话人长片段的平均声纹做二次合并：同一个人的不同簇会被并到一起。

    短片段声纹不稳定，只拿时长足够的片段算质心，避免把不同人误合。
    """
    import numpy as np
    from scipy.cluster.hierarchy import fcluster, linkage

    groups: dict[int, list] = {}
    for turn, vec in zip(turns, vectors):
        if vec is not None and (turn.end - turn.start) >= min_turn:
            groups.setdefault(turn.speaker, []).append(vec)

    keys = [k for k, v in groups.items() if v]
    if len(keys) < 2:
        return turns

    centroids = np.stack([np.mean(groups[k], axis=0) for k in keys])
    centroids /= np.maximum(np.linalg.norm(centroids, axis=1, keepdims=True), 1e-9)

    tree = linkage(centroids, method="average", metric="cosine")
    labels = fcluster(tree, t=max(0.05, 1.0 - sim_threshold), criterion="distance")

    label_map: dict[int, int] = {}
    for label in labels:
        label_map.setdefault(int(label), len(label_map))
    speaker_to_new = {key: label_map[int(label)]
                      for key, label in zip(keys, labels)}

    merged: list[Turn] = []
    extra: dict[int, int] = {}
    for turn in turns:
        new_id = speaker_to_new.get(turn.speaker)
        if new_id is None:
            new_id = extra.setdefault(turn.speaker,
                                      len(speaker_to_new) + len(extra))
        merged.append(Turn(turn.start, turn.end, new_id))
    return merged


def diarize(audio_16k: Path, seg_model: Path, emb_model: Path,
            threshold: float = 0.9, num_speakers: int = -1,
            provider: str = "cpu",
            min_duration_on: float = 0.25,
            min_duration_off: float = 0.5,
            merge_sim: float = 0.8) -> list[Turn]:
    import sherpa_onnx

    samples = load_audio_16k(audio_16k)

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(seg_model)),
            provider=provider,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(emb_model),
            provider=provider,
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers,
            threshold=threshold,
        ),
        min_duration_on=min_duration_on,
        min_duration_off=min_duration_off,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(config)
    result = sd.process(samples).sort_by_start_time()
    turns = [Turn(float(r.start), float(r.end), int(r.speaker)) for r in result]

    if merge_sim and len(turns) >= 3:
        vectors = _extract_embeddings(samples, turns, emb_model, provider)
        turns = _merge_similar(turns, vectors, merge_sim)
    return turns


def diarize_subprocess(audio_16k: Path, seg_model: Path, emb_model: Path,
                       **kwargs) -> list[Turn]:
    """在独立子进程里跑识别：sherpa 推理期间会一直占着 GIL，放子进程界面才不卡。"""
    import json
    import os
    import subprocess
    import sys
    import tempfile

    out_path = Path(tempfile.gettempdir()) / f"speakersplit_diarize_{os.getpid()}.json"
    payload = json.dumps(kwargs, ensure_ascii=False)
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--diarize", str(audio_16k), str(seg_model),
               str(emb_model), str(out_path), payload]
    else:
        cmd = [sys.executable, "-m", "app.diarize_cli", str(audio_16k),
               str(seg_model), str(emb_model), str(out_path), payload]

    subprocess.run(cmd, check=True, timeout=None)
    raw = json.loads(out_path.read_text(encoding="utf-8"))
    try:
        out_path.unlink()
    except OSError:
        pass
    return [Turn(float(a), float(b), int(s)) for a, b, s in raw]

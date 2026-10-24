"""GC walkthrough video -> SiteEvidence.

Frames are sampled in code (PyAV), described by Claude, and filtered in code:
only estimates for real line items, marked visible, in range, and above a
confidence floor reach SiteEvidence. Matching evidence to a pay request and
deciding what a gap means belong to the kernel (rule 13).
"""

import hashlib
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import anthropic
import av
from PIL import Image

from perception import llm
from perception.schema import LineItem, SiteEvidence

MAX_EDGE_PX = 1568  # larger images are downscaled by the API anyway
DEFAULT_FRAMES = 10
DEFAULT_MIN_CONFIDENCE = 0.5


class VideoError(RuntimeError):
    pass


@dataclass
class EvidenceNote:
    code: str
    message: str
    item_no: str | None = None


@dataclass
class EvidenceResult:
    evidence: SiteEvidence
    date_source: str  # "recorded" (video metadata) or "upload"
    notes: list[EvidenceNote] = field(default_factory=list)
    raw: llm.ProgressEstimate | None = None


def _recorded_date(container) -> date | None:
    """creation_time from container or stream metadata, in UTC.

    It can reflect when the file was exported rather than filmed, so it is
    trusted only when it is not later than the upload.
    """
    sources = [container.metadata] + [s.metadata for s in container.streams.video]
    for meta in sources:
        value = (meta or {}).get("creation_time")
        if value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
            except ValueError:
                continue
    return None


def _to_jpeg(frame: av.VideoFrame) -> bytes:
    img: Image.Image = frame.to_image()
    rotation = getattr(frame, "rotation", 0) or 0
    if rotation:
        # Phone video stores portrait footage as rotated landscape frames.
        img = img.rotate(rotation, expand=True)
    img.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue()


def sample_frames(video_path: Path, n: int = DEFAULT_FRAMES) -> tuple[list[tuple[float, bytes]], date | None]:
    """Return n frames evenly spaced across the video, plus its recorded date if present."""
    if n < 1:
        raise ValueError("n must be at least 1")
    try:
        container = av.open(str(video_path))
    except (av.error.FFmpegError, OSError) as e:
        raise VideoError(f"could not open video: {e}") from e
    with container:
        if not container.streams.video:
            raise VideoError("file has no video stream")
        stream = container.streams.video[0]
        recorded = _recorded_date(container)

        duration = None
        if stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        elif container.duration:
            duration = container.duration / av.time_base
        if not duration or duration <= 0:
            raise VideoError("video has no usable duration")

        # Midpoints of n equal slices: avoids the black first frame and the fumbled last one.
        targets = [duration * (i + 0.5) / n for i in range(n)]
        frames: list[tuple[float, bytes]] = []
        for t in targets:
            container.seek(int(t / stream.time_base), stream=stream, backward=True, any_frame=False)
            chosen = None
            for frame in container.decode(stream):
                if frame.time is None:
                    continue
                chosen = frame
                if frame.time >= t:
                    break
            if chosen is None:
                continue
            if frames and abs(frames[-1][0] - chosen.time) < 1e-3:
                continue  # very short videos can map several targets onto one frame
            frames.append((float(chosen.time), _to_jpeg(chosen)))
    if not frames:
        raise VideoError("no frames could be decoded")
    return frames, recorded


def build_site_evidence(
    video_path: str | Path,
    *,
    job_id: str,
    upload_date: date,
    line_items: list[LineItem],
    frames_dir: str | Path,
    n_frames: int = DEFAULT_FRAMES,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    client=None,
) -> EvidenceResult:
    """Estimate visible progress for a job's line items from one walkthrough video.

    line_items is the job's schedule of values, used only for item numbers and
    descriptions; the sub's claimed percentages are deliberately not shown to
    the model, so the estimate is independent of the claim it will be checked against.
    """
    if not line_items:
        raise ValueError("line_items is empty")
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", job_id):
        raise ValueError(f"job_id must be a plain identifier: {job_id!r}")
    notes: list[EvidenceNote] = []
    frames, recorded = sample_frames(Path(video_path), n_frames)

    if recorded and recorded <= upload_date:
        evidence_date, date_source = recorded, "recorded"
    else:
        evidence_date, date_source = upload_date, "upload"
        if recorded:
            notes.append(EvidenceNote("recorded_after_upload", f"metadata date {recorded} is after upload {upload_date}; using upload date"))

    # Keyed by file hash so a second video on the same day doesn't overwrite the first one's frames.
    digest = hashlib.sha256()
    with open(video_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    out = Path(frames_dir) / job_id / f"{evidence_date.isoformat()}_{digest.hexdigest()[:12]}"
    out.mkdir(parents=True, exist_ok=True)
    frame_paths = []
    for i, (ts, jpeg) in enumerate(frames):
        p = out / f"frame_{i:02d}_{ts:07.1f}s.jpg"
        p.write_bytes(jpeg)
        frame_paths.append(str(p))

    try:
        raw = llm.estimate_progress(frames, [(li.item_no, li.description) for li in line_items], client)
    except (llm.PerceptionLLMError, anthropic.APIError) as e:
        raise VideoError(f"progress estimate failed: {e}") from e

    known = {li.item_no for li in line_items}
    counts: dict[str, int] = {}
    for est in raw.estimates:
        counts[est.item_no] = counts.get(est.item_no, 0) + 1

    estimates: dict[str, float] = {}
    for est in raw.estimates:
        if est.item_no not in known:
            notes.append(EvidenceNote("unknown_item", f"model returned item {est.item_no!r}, not in the schedule of values", est.item_no))
        elif counts[est.item_no] > 1:
            notes.append(EvidenceNote("duplicate_estimate", "model returned more than one estimate; none used", est.item_no))
        elif not est.visible:
            notes.append(EvidenceNote("not_visible", est.observations, est.item_no))
        elif not (0 <= est.pct_complete <= 100 and 0 <= est.confidence <= 1):
            notes.append(EvidenceNote("out_of_range", f"pct {est.pct_complete}, confidence {est.confidence}", est.item_no))
        elif est.confidence < min_confidence:
            notes.append(EvidenceNote("low_confidence", f"confidence {est.confidence:.2f} below {min_confidence}", est.item_no))
        else:
            estimates[est.item_no] = est.pct_complete
    for item_no in sorted(known - counts.keys()):
        notes.append(EvidenceNote("no_estimate", "model returned nothing for this item", item_no))

    evidence = SiteEvidence(job_id=job_id, date=evidence_date, frames=frame_paths, estimates=estimates)
    return EvidenceResult(evidence, date_source, notes, raw)

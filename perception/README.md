# perception

Turns documents and video into typed evidence. It never decides anything and never touches payments.

| Function | Input | Output |
|---|---|---|
| `extract_pay_request(pdf_path, *, sub_id, job_id, submitted_at, request_id=None, allow_llm_fallback=True)` | One pay application PDF (G703 continuation sheet + G702 summary), plus sub and job from the submission | `ExtractionResult`: `pay_request` (`PayRequest`), `method`, `notes`, `sha256`, `fallback_reason` |
| `build_site_evidence(video_path, *, job_id, upload_date, line_items, frames_dir, n_frames=10, min_confidence=0.5)` | One walkthrough video from the GC, plus the job's line items | `EvidenceResult`: `evidence` (`SiteEvidence`), `date_source`, `notes`, `raw` |

Both raise (`ExtractionError` / `NotAG703` / `VideoError`) rather than return a guess.

```
python -m perception g703 tests/fixtures/perception/g703_clean.pdf [--no-fallback]
python -m perception video walk.mp4 --job job_elm --sov payapp.pdf
python -m pytest tests/perception -q
```

Dependencies are in the root `requirements.txt`. Claude calls use the standard `ANTHROPIC_API_KEY` environment variable; `PERCEPTION_MODEL` (default `claude-opus-5-5`) and `PERCEPTION_EFFORT` (default `medium`) override the model settings.

## Pay app extraction

```
PDF ─► text layer readable? ─yes─► pdfplumber table ─► RawSheet (printed strings) ─┐
          │ no / untrustworthy                                                       ├─► numbers.py ─► LineItems ─► checks ─► PayRequest
          └──────────────────────► Claude transcription ─► RawSheet ──────────────────┘
```

- **Text layer first.** The table is read with pdfplumber and columns are mapped by header wording, not position.
- **Only visible text counts.** A character is kept only if it leaves ink when the page is rendered. This removes invisible, white, transparent and covered text, which could otherwise make the parser read a different number than the GC sees.
- **Claude fallback.** It is used when the text layer is missing, is an OCR layer over a scan, has no recognisable header, has an unparseable cell, or when most lines fail `D+E+F=G` and `C−G=H` at once (a sign of shifted columns). Claude transcribes cells as printed strings, and the same parser reads them. A transcription with any uncertain cell is rejected rather than used.
- **One number parser.** Values are kept as printed (`Decimal`, never rounded), or the cell fails. Blank cells and lone dashes are `0`, and parentheses are negative. Whitespace between digits means two values were merged into one cell, so it fails.
- **Checks report, never correct.** `D+E+F=G`, `C−G=H`, `G÷C`, grand totals and duplicate item numbers are checked and reported as `notes`. A sub's arithmetic error is evidence for the kernel, so the printed values pass through unchanged.
- **Sub and job come from the submission.** A PDF can claim to be from anyone, so `sub_id` and `job_id` are never read from the document.
- **Claims come from the document.** The application number, period, G702 summary and remit-to account are read from the document, because they are the sub's claims the kernel checks. Rule 10, for example, checks the remit-to account. Pages that disagree on the period, the account or the application number raise an error rather than pick one.
- **G702 is cross-checked.** G702 lines 3, 6 and 8 and the G702-vs-G703 totals are checked, with mismatches reported as `notes`. A sheet with no retainage column, no G702 or no remit-to account raises an error, because the schema needs all three and a guessed value would read as a sub's error.
- **Tested against B's documents.** `tests/perception/test_synthetic_roundtrip.py` renders every synthetic pay application from `synthetic/` and requires an exact read-back against its `truth.json`, in all three number styles. It is skipped until `synthetic/` is on the branch.

## Site evidence

- **Frames:** sampled at the midpoints of equal slices of the video (PyAV), rotated upright and downscaled to 1568 px on the long edge. They are saved under `frames_dir/<job_id>/<date>_<video hash>/`.
- **What Claude sees:** the frames and the line items as a JSON list of one-line labels. It never sees the sub's claimed percentages, so the estimate is independent of the claim it is checked against.
- **What reaches `SiteEvidence.estimates`:** only estimates that are for a known item, marked visible, within 0–100, and at or above `min_confidence`. Everything else goes to `notes`.
- **Date:** the video's recorded date (metadata, UTC) is used unless it is later than the upload date. Otherwise the upload date is used, and `date_source` says which.

## Open questions for the schema (`shared/schema.py`)

1. **No confidence on `SiteEvidence`.** `SiteEvidence` has no per-item confidence and no extraction method or provenance field. They live in the result wrappers only, so the kernel and event log can't see them unless fields are added.
2. **No G703-only sheets.** The schema requires per-line retainage and a G702, so a G703 that lacks either fails extraction rather than being read partially.

## Known limits

- **Borderless tables.** These currently always go to the Claude fallback.
- **Swapped D/E/F columns.** A swap of the previous / this-period / stored columns would pass every arithmetic check, because `D+E+F` is symmetric. The header mapping is the only guard.
- **Claude paths untested live.** The Claude calls are covered with a fake client only; no live request has been made yet.

# perception

Turns documents and video into typed evidence. It never decides anything and never touches payments.

| Function | Input | Output |
|---|---|---|
| `extract_pay_request(pdf_path, *, sub_id, job_id, submitted_at, bank_account=None, request_id=None, allow_llm_fallback=True)` | One G703 continuation-sheet PDF, plus identity fields from the submission | `ExtractionResult`: `pay_request` (`PayRequest`), `method`, `notes`, `sha256`, `fallback_reason` |
| `build_site_evidence(video_path, *, job_id, upload_date, line_items, frames_dir, n_frames=10, min_confidence=0.5)` | One walkthrough video from the GC, plus the job's line items | `EvidenceResult`: `evidence` (`SiteEvidence`), `date_source`, `notes`, `raw` |

Both raise (`ExtractionError` / `NotAG703` / `VideoError`) rather than return a guess.

```
python -m perception g703 tests/fixtures/perception/g703_clean.pdf [--no-fallback]
python -m perception video walk.mp4 --job job_elm --sov payapp.pdf
python -m pytest tests/perception -q
```

Dependencies: `pip install -r perception/requirements.txt`. Claude calls use the standard `ANTHROPIC_API_KEY` environment variable; `PERCEPTION_MODEL` (default `claude-opus-5-5`) and `PERCEPTION_EFFORT` (default `medium`) override the model settings.

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
- **Identity comes from the submission.** `sub_id`, `job_id` and `bank_account` come from the submission, not from the document, because a PDF can claim to be from anyone.

## Site evidence

- **Frames:** sampled at the midpoints of equal slices of the video (PyAV), rotated upright and downscaled to 1568 px on the long edge. They are saved under `frames_dir/<job_id>/<date>_<video hash>/`.
- **What Claude sees:** the frames and the line items as a JSON list of one-line labels. It never sees the sub's claimed percentages, so the estimate is independent of the claim it is checked against.
- **What reaches `SiteEvidence.estimates`:** only estimates that are for a known item, marked visible, within 0–100, and at or above `min_confidence`. Everything else goes to `notes`.
- **Date:** the video's recorded date (metadata, UTC) is used unless it is later than the upload date. Otherwise the upload date is used, and `date_source` says which.

## Open questions for the schema (`shared/schema.py`)

Until `shared/schema.py` exists, perception validates against `_provisional_schema.py`. `perception/schema.py` switches to the shared module automatically once it does.

1. **Money type.** Money is `Decimal` here, and floats would introduce rounding error. In JSON it serialises as strings.
2. **`pct_complete` units.** These are percentage points (0–100), as printed, to match `progress_tolerance_points`.
3. **Optional retainage.** `LineItem.retainage` is optional because some G703s have no retainage column.
4. **No confidence field.** `SiteEvidence` has no field for per-item confidence or for an extraction method or provenance. Today these live only in the result wrappers, so the kernel and event log cannot see them unless fields are added.
5. **`g702` is unset.** This module reads G703 sheets only.

## Known limits

- **Borderless tables.** These currently always go to the Claude fallback.
- **Swapped D/E/F columns.** A swap of the previous / this-period / stored columns would pass every arithmetic check, because `D+E+F` is symmetric. The header mapping is the only guard.
- **Claude paths untested live.** The Claude calls are covered with a fake client only; no live request has been made yet.

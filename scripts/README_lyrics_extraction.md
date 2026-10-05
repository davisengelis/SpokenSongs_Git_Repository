# Lyrics Extraction with Claude

The runner compares direct Claude image transcription with a local Tesseract OCR
transcription refined by Claude. The default sample manifest lists the 10
selected songs. It reads scans from a local folder; it does not copy images into
the repository. Images are sent to Anthropic only when extraction runs.

## Requirements

- Python 3.10 or later
- An Anthropic API key with access to a Claude Sonnet model
- Tesseract OCR and Latvian language data (`lav`)

Install the Python dependencies from the repository root:

```bash
python -m pip install -r requirements-lyrics-extraction.txt
```

On Ubuntu/Debian, install the OCR engine and Latvian data with:

```bash
sudo apt install tesseract-ocr tesseract-ocr-lav
```

Set the API key in the environment; do not put it in source files:

```bash
export ANTHROPIC_API_KEY="your-key"
```

## Run the 10-song sample

Pass the local folder containing `book_data/` as the image root. The default
manifest is `scripts/lyrics_extraction_sample.txt`.

```bash
python scripts/lyrics_extract_with_claude.py /path/to/local/scans --dry-run
python scripts/lyrics_extract_with_claude.py /path/to/local/scans
```

The runner discovers the newest Sonnet model available to the API key. To pin a
model, pass `--model MODEL_ID` or set `ANTHROPIC_MODEL`. Results default to
`data/ocr/lyrics_extraction.csv`, which is ignored by Git. The CSV has one row
per song, with separate columns for each method and model. Re-running resumes
incomplete extractions and retries failures. Each result also gets a
`review_required` flag when its uncertainty reaches `--review-threshold`
(default: `0.25`); adjust the threshold after inspecting the sample.

## Run the full collection

Use `--all` only after reviewing the sample results. It recursively selects all
PNG, JPG, and JPEG files under the image root.

```bash
python scripts/lyrics_extract_with_claude.py /path/to/local/scans --all
```

Use `--output` to choose another CSV. To compare models, rerun with a different
`--model` and the same output path; each model receives its own CSV columns.
Use `--max-workers` to limit parallel API requests (default: 2).
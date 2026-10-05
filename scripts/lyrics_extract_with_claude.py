"""Extract song metadata and lyrics from Latvian image scans with Claude.

The default manifest contains the project's 10-song comparison sample. Use
--all explicitly to process every supported image under the image root.
"""

from __future__ import annotations

import argparse
import base64
from io import BytesIO
import csv
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}
DEFAULT_MANIFEST = Path(__file__).with_name("lyrics_extraction_sample.txt")
DEFAULT_OUTPUT = Path("data/ocr/lyrics_extraction.csv")
METHODS = ("direct", "ocr")
RESULT_FIELDS = (
    "metadata",
    "lyrics",
    "uncertainty",
    "challenge_comment",
    "review_required",
    "error",
)
MAX_IMAGE_BYTES = 4_800_000
MAX_IMAGE_DIMENSION = 7_900

SYSTEM_PROMPT = """You transcribe Latvian printed song pages. Return only a JSON object with
these string/number fields: metadata, lyrics, uncertainty, challenge_comment.

metadata: transcribe the single metadata line at the top right verbatim; do not
translate, normalize, or split it into fields. Use an empty string if absent.
lyrics: transcribe all visible lyrics, retaining line breaks and stanza breaks.
Do not include titles, page numbers, or metadata as lyrics. Use [unreadable]
where text cannot be read; never guess missing words.
uncertainty: one overall number from 0 (certain) to 1 (highly uncertain).
challenge_comment: briefly describe any unreadable, ambiguous, or layout issue;
use an empty string if there was no notable difficulty."""


def load_image_paths(image_root: Path, manifest: Path | None, all_images: bool) -> list[Path]:
    if all_images:
        paths = sorted(
            path
            for path in image_root.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )
    else:
        if manifest is None:
            raise ValueError("A manifest is required unless --all is used.")
        paths = []
        for line in manifest.read_text(encoding="utf-8").splitlines():
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            path = Path(entry)
            paths.append(path if path.is_absolute() else image_root / path)

    if not paths:
        raise ValueError("No image paths were selected.")

    missing = [path for path in paths if not path.is_file()]
    if missing:
        preview = "\n".join(f"  {path}" for path in missing[:10])
        suffix = "\n  ..." if len(missing) > 10 else ""
        raise FileNotFoundError(f"Missing {len(missing)} image(s):\n{preview}{suffix}")

    unsupported = [path for path in paths if path.suffix.lower() not in IMAGE_EXTENSIONS]
    if unsupported:
        raise ValueError(f"Unsupported image extension: {unsupported[0].suffix}")

    return sorted(set(path.resolve() for path in paths))


def relative_image_path(image_path: Path, image_root: Path) -> str:
    try:
        return image_path.relative_to(image_root.resolve()).as_posix()
    except ValueError:
        return str(image_path)


def model_fields(model: str, method: str) -> list[str]:
    prefix = f"{method}__{model}__"
    return [f"{prefix}{field}" for field in RESULT_FIELDS]


def read_results(output_path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not output_path.exists():
        return [], ["song_id", "image_path"]

    with output_path.open(encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        fieldnames = reader.fieldnames or ["song_id", "image_path"]
        rows = [dict(row) for row in reader]
    return rows, fieldnames


def write_results(output_path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=output_path.parent,
            delete=False,
        ) as csv_file:
            temporary_path = Path(csv_file.name)
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def parse_response(text: str) -> dict[str, str]:
    json_start = text.find("{")
    if json_start < 0:
        raise ValueError("Claude response did not contain a JSON object.")
    result, _ = json.JSONDecoder().raw_decode(text[json_start:])
    if not isinstance(result, dict):
        raise ValueError("Claude response was not a JSON object.")

    metadata = result.get("metadata", "")
    lyrics = result.get("lyrics", "")
    challenge_comment = result.get("challenge_comment", "")
    uncertainty = result.get("uncertainty")
    if not all(isinstance(value, str) for value in (metadata, lyrics, challenge_comment)):
        raise ValueError("Claude returned invalid text fields.")
    if isinstance(uncertainty, bool) or not isinstance(uncertainty, (float, int)):
        raise ValueError("Claude returned an invalid uncertainty score.")
    if not 0 <= uncertainty <= 1:
        raise ValueError("Claude uncertainty must be between 0 and 1.")

    return {
        "metadata": metadata,
        "lyrics": lyrics,
        "uncertainty": str(float(uncertainty)),
        "challenge_comment": challenge_comment,
        "review_required": "",
        "error": "",
    }


def ocr_text(image_path: Path) -> str:
    try:
        from PIL import Image
        import pytesseract
    except ImportError as error:
        raise RuntimeError(
            "OCR dependencies are missing. Install requirements-lyrics-extraction.txt."
        ) from error

    with Image.open(image_path) as image:
        width, height = image.size
        metadata_region = image.crop((width // 2, 0, width, height // 3))
        metadata_text = pytesseract.image_to_string(
            metadata_region,
            lang="lav",
            config="--psm 6",
        ).strip()
        page_text = pytesseract.image_to_string(image, lang="lav").strip()
    return f"Top-right metadata-region OCR:\n{metadata_text}\n\nFull-page OCR:\n{page_text}"


def image_media_type(image_path: Path) -> str:
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}[
        image_path.suffix.lower()
    ]


def image_payload(image_path: Path) -> tuple[str, bytes]:
    image_bytes = image_path.read_bytes()
    try:
        from PIL import Image, ImageOps
    except ImportError as error:
        raise RuntimeError(
            "Pillow is missing. Install requirements-lyrics-extraction.txt."
        ) from error

    with Image.open(image_path) as source:
        if (
            len(image_bytes) <= MAX_IMAGE_BYTES
            and max(source.size) <= MAX_IMAGE_DIMENSION
        ):
            return image_media_type(image_path), image_bytes

        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION))
        for quality in (92, 88, 84, 80, 76):
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=quality, optimize=True)
            image_bytes = buffer.getvalue()
            if len(image_bytes) <= MAX_IMAGE_BYTES:
                return "image/jpeg", image_bytes

    raise ValueError("Image is too large to prepare under Anthropic's image limit.")

def request_with_retries(client: Any, **request: Any) -> Any:
    for attempt in range(5):
        try:
            return client.messages.create(**request)
        except Exception as error:
            status_code = getattr(error, "status_code", None)
            retryable = status_code in {408, 409, 429} or (
                isinstance(status_code, int) and status_code >= 500
            ) or error.__class__.__name__ in {"APIConnectionError", "APITimeoutError"}
            if not retryable or attempt == 4:
                raise
            time.sleep(min(2**attempt, 30))


def extract_one(
    client: Any,
    model: str,
    image_path: Path,
    method: str,
) -> dict[str, str]:
    if method == "direct":
        media_type, image_bytes = image_payload(image_path)
        encoded_image = base64.b64encode(image_bytes).decode("ascii")
        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": encoded_image,
                },
            },
            {"type": "text", "text": "Transcribe the metadata and lyrics from this image."},
        ]
    else:
        transcription = ocr_text(image_path)
        user_content = (
            "The following is Latvian Tesseract OCR from a song-page image. "
            "Correct clear OCR errors using context, but do not invent text. "
            "The original page layout is not available; use an empty metadata "
            "value if the top-right metadata line cannot be identified.\n\n"
            f"{transcription}"
        )

    response = request_with_retries(
        client,
        model=model,
        max_tokens=2500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    response_text = "\n".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )
    return parse_response(response_text)


def resolve_model(client: Any, configured_model: str | None) -> str:
    if configured_model:
        return configured_model

    available = list(client.models.list(limit=100))
    sonnet_models = [model for model in available if "sonnet" in model.id.lower()]
    if not sonnet_models:
        raise RuntimeError(
            "No Sonnet model was returned by Anthropic. Pass --model with an available model ID."
        )
    return max(sonnet_models, key=lambda model: model.created_at).id


def is_complete(row: dict[str, str], fields: list[str]) -> bool:
    return bool(row.get(fields[2])) and not row.get(fields[-1])


def run(args: argparse.Namespace) -> int:
    image_root = args.image_root.resolve()
    if not image_root.is_dir():
        raise NotADirectoryError(f"Image root is not a directory: {image_root}")
    manifest = args.manifest.resolve() if args.manifest else None
    if manifest and not manifest.is_file():
        raise FileNotFoundError(f"Manifest does not exist: {manifest}")

    image_paths = load_image_paths(image_root, manifest, args.all_images)
    if args.dry_run:
        print(f"Validated {len(image_paths)} image(s) under {image_root}")
        print(f"Results will be written to {args.output.resolve()}")
        print("No images were sent and no API calls were made.")
        return 0

    try:
        import anthropic
    except ImportError as error:
        raise RuntimeError(
            "Anthropic SDK is missing. Install requirements-lyrics-extraction.txt."
        ) from error

    client = anthropic.Anthropic(max_retries=0)
    model = resolve_model(client, args.model or os.environ.get("ANTHROPIC_MODEL"))
    print(f"Using Anthropic model: {model}")

    output_path = args.output.resolve()
    rows, fieldnames = read_results(output_path)
    row_by_path = {row.get("image_path", ""): row for row in rows}
    tasks: list[tuple[Path, str, list[str]]] = []

    for image_path in image_paths:
        relative_path = relative_image_path(image_path, image_root)
        row = row_by_path.setdefault(
            relative_path,
            {"song_id": image_path.stem, "image_path": relative_path},
        )
        for method in METHODS:
            columns = model_fields(model, method)
            for column in columns:
                if column not in fieldnames:
                    fieldnames.append(column)
            if not is_complete(row, columns):
                tasks.append((image_path, method, columns))

    rows = list(row_by_path.values())
    write_results(output_path, rows, fieldnames)
    if not tasks:
        print(f"All selected extractions are already complete: {output_path}")
        return 0

    print(f"Processing {len(image_paths)} song image(s), {len(tasks)} extraction(s).")
    failures = 0
    with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(extract_one, client, model, image_path, method): (
                image_path,
                method,
                columns,
            )
            for image_path, method, columns in tasks
        }
        for future in as_completed(futures):
            image_path, method, columns = futures[future]
            relative_path = relative_image_path(image_path, image_root)
            row = row_by_path[relative_path]
            try:
                result = future.result()
            except Exception as error:
                failures += 1
                result = {field: "" for field in RESULT_FIELDS}
                result["error"] = f"{type(error).__name__}: {error}"
                print(f"ERROR {relative_path} ({method}): {result['error']}", file=sys.stderr)

            if result["uncertainty"]:
                result["review_required"] = (
                    "yes"
                    if float(result["uncertainty"]) >= args.review_threshold
                    else "no"
                )
            for field, column in zip(RESULT_FIELDS, columns):
                row[column] = result[field]
            write_results(output_path, rows, fieldnames)
            print(f"Saved {relative_path} ({method})")

    if failures:
        print(f"Finished with {failures} failed extraction(s); rerun to retry them.", file=sys.stderr)
        return 1
    print(f"Finished successfully: {output_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract metadata and lyrics from song-page images with Claude."
    )
    parser.add_argument(
        "image_root",
        type=Path,
        help="Local folder containing the scans; image files stay in this folder.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Text file with one image path per line, relative to image_root.",
    )
    parser.add_argument(
        "--all",
        dest="all_images",
        action="store_true",
        help="Process all PNG/JPG/JPEG images recursively instead of the sample manifest.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Resumable CSV path. Default: {DEFAULT_OUTPUT}.",
    )
    parser.add_argument(
        "--model",
        help="Anthropic model ID to pin; otherwise use ANTHROPIC_MODEL or discover latest Sonnet.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=2,
        help="Maximum parallel API requests (default: 2).",
    )
    parser.add_argument(
        "--review-threshold",
        type=float,
        default=0.25,
        help="Flag results at or above this uncertainty score for human review (default: 0.25).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate selected image files without OCR or API calls.",
    )
    args = parser.parse_args()
    if not 1 <= args.max_workers <= 8:
        parser.error("--max-workers must be between 1 and 8.")
    if not 0 <= args.review_threshold <= 1:
        parser.error("--review-threshold must be between 0 and 1.")
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args()))
    except (FileNotFoundError, NotADirectoryError, ValueError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
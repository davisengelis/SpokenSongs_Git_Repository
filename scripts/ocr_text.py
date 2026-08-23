from pathlib import Path
import argparse
import sys

import pytesseract
from PIL import Image


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def find_images(folder: Path, contains: str, recursive: bool = False):
    if recursive:
        files = folder.rglob("*")
    else:
        files = folder.iterdir()

    contains = contains.lower()

    return sorted(
        file
        for file in files
        if file.is_file()
        and file.suffix.lower() in IMAGE_EXTENSIONS
        and contains in file.name.lower()
    )


def ocr_image(image_path: Path, lang: str = "lav", psm: int | None = None):
    config = ""

    if psm is not None:
        config += f" --psm {psm}"

    with Image.open(image_path) as img:
        text = pytesseract.image_to_string(img, lang=lang, config=config)

    return text.strip()


def make_output_path(image_path: Path, suffix: str = ""):
    if suffix:
        return image_path.with_name(f"{image_path.stem}{suffix}.txt")

    return image_path.with_suffix(".txt")


def main():
    parser = argparse.ArgumentParser(
        description="Batch OCR images containing a chosen string in their filename."
    )

    parser.add_argument(
        "folder",
        nargs="?",
        default=".",
        help="Folder to search. Default: current folder.",
    )

    parser.add_argument(
        "--contains",
        default="teksti",
        help="Only OCR files whose filename contains this text. Default: teksti.",
    )

    parser.add_argument(
        "--lang",
        default="lav",
        help="Tesseract language code. Default: lav.",
    )

    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Search subfolders too.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing .txt files.",
    )

    parser.add_argument(
        "--suffix",
        default="",
        help="Optional suffix for output files, for example: --suffix _ocr",
    )

    parser.add_argument(
        "--psm",
        type=int,
        default=None,
        help="Optional Tesseract page segmentation mode, for example: --psm 6",
    )

    parser.add_argument(
        "--tesseract-cmd",
        default=None,
        help="Optional full path to the Tesseract executable.",
    )

    args = parser.parse_args()

    if args.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = args.tesseract_cmd

    folder = Path(args.folder).resolve()

    if not folder.exists():
        print(f"Folder does not exist: {folder}", file=sys.stderr)
        return 1

    images = find_images(
        folder=folder,
        contains=args.contains,
        recursive=args.recursive,
    )

    if not images:
        print(f"No matching images found in: {folder}")
        print(f"Looking for: *{args.contains}*.png / .jpg / .jpeg")
        return 0

    print(f"Found {len(images)} matching image file(s).")
    print()

    failures = 0

    for index, image_path in enumerate(images, start=1):
        output_path = make_output_path(image_path, suffix=args.suffix)

        print(f"[{index}/{len(images)}] OCR: {image_path.name}")

        if output_path.exists() and not args.overwrite:
            print(f"    Skipping, output already exists: {output_path.name}")
            continue

        try:
            text = ocr_image(
                image_path=image_path,
                lang=args.lang,
                psm=args.psm,
            )

            output_path.write_text(text + "\n", encoding="utf-8")

            print(f"    Saved: {output_path.name}")

        except Exception as error:
            failures += 1
            print(f"    ERROR: {error}", file=sys.stderr)

    print()

    if failures:
        print(f"Finished with {failures} error(s).", file=sys.stderr)
        return 1

    print("Finished successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
lyrics_ocr_correction_prototype.py

First conservative correction prototype for the Spoken Songs OCR/OMR workflow.

What it does:
  * pairs PNG and MusicXML files by filename stem;
  * detects staff systems and lyric regions;
  * OCRs verse 1 with Tesseract (lav);
  * preserves printed syllable separators where visible;
  * uses Pyphen only as a fallback for likely lost boundaries;
  * evaluates confidence PER SYSTEM;
  * writes corrected XML only if EVERY detected system is HIGH or MEDIUM;
  * otherwise copies the original XML unchanged to review/;
  * always writes a CSV report;
  * supports up to 4 staff systems in the report (system_1 ... system_4).

IMPORTANT:
  * Original XML files are NEVER modified.
  * Verse 2+ lyrics are left untouched.
  * Only <lyric number="1"> text content is replaced.
  * This is still a prototype intended for testing on a larger dataset.

Requirements:
    pip install pytesseract opencv-python pyphen
    Tesseract with Latvian ("lav") trained data installed.

Example:
    python lyrics_ocr_correction_prototype.py \
        --png-dir input/png \
        --xml-dir input/xml \
        --output-dir output

Output:
    output/
      corrected/
      review/
      debug/
      correction_report.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sys
from difflib import SequenceMatcher
from pathlib import Path
import xml.etree.ElementTree as ET

import cv2
import numpy as np
import pyphen
import pytesseract


LATVIAN_LETTERS = "A-Za-zĀāČčĒēĢģĪīĶķĻļŅņŠšŪūŽžŌō"
LETTER_RE = re.compile(fr"[{LATVIAN_LETTERS}]")
VERSE1_MARKER_RE = re.compile(r"(?<!\d)(?:1|I|l)\s*[.,]\s*", re.UNICODE)
HYPHENATOR = pyphen.Pyphen(lang="lv_LV")


def normalize_text(s: str) -> str:
    s = s.lower()
    s = s.replace("_", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(fr"[^{LATVIAN_LETTERS}'’ -]", "", s)
    return s.strip()


def detect_staff_systems(gray):
    _, inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    width = gray.shape[1]
    kernel_len = max(45, width // 25)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_len, 1))
    horizontal = cv2.morphologyEx(inv, cv2.MORPH_OPEN, kernel)

    row_ink = (horizontal > 0).sum(axis=1)
    candidate_rows = np.where(row_ink > width * 0.12)[0]

    groups = []
    if len(candidate_rows):
        start = prev = int(candidate_rows[0])
        for y in candidate_rows[1:]:
            y = int(y)
            if y - prev <= 3:
                prev = y
            else:
                groups.append((start, prev))
                start = prev = y
            prev = y
        groups.append((start, prev))

    centers = [(a + b) // 2 for a, b in groups]

    systems, current = [], []
    for y in centers:
        if not current or y - current[-1] < 36:
            current.append(y)
        else:
            if 4 <= len(current) <= 6:
                systems.append(current)
            current = [y]
    if 4 <= len(current) <= 6:
        systems.append(current)

    return systems


def make_lyric_bands(gray, systems):
    bands = []
    h = gray.shape[0]

    for i, system in enumerate(systems):
        bottom = max(system)
        y1 = min(h, bottom + 12)

        if i + 1 < len(systems):
            next_top = min(systems[i + 1])
            available = next_top - y1
            y2 = min(next_top - 20, y1 + min(135, max(75, int(available * 0.66))))
        else:
            y2 = min(h, y1 + 135)

        bands.append((y1, y2, gray[y1:y2, :]))

    return bands


def preprocess_for_ocr(gray):
    up = cv2.resize(gray, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return bw


def ocr_lines(crop):
    processed = preprocess_for_ocr(crop)
    outputs = []

    for psm in (3, 6, 11, 12):
        text = pytesseract.image_to_string(
            processed,
            lang="lav",
            config=f"--psm {psm} -c preserve_interword_spaces=1",
        )
        outputs.extend(line.strip() for line in text.splitlines() if line.strip())

    unique, seen = [], set()
    for line in outputs:
        key = normalize_text(line)
        if key and key not in seen:
            unique.append(line)
            seen.add(key)

    return unique, processed


def read_xml_structure(xml_path: Path):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    systems = [[]]
    lyric_elements = [[]]

    for measure in root.findall(".//measure"):
        print_el = measure.find("print")
        if print_el is not None and print_el.get("new-system") == "yes" and systems[-1]:
            systems.append([])
            lyric_elements.append([])

        for note in measure.findall("note"):
            lyric = None
            for candidate in note.findall("lyric"):
                if candidate.get("number") == "1":
                    lyric = candidate
                    break

            if lyric is None:
                continue

            text_el = lyric.find("text")
            syllabic_el = lyric.find("syllabic")

            systems[-1].append({
                "text": "" if text_el is None or text_el.text is None else text_el.text,
                "syllabic": "" if syllabic_el is None or syllabic_el.text is None else syllabic_el.text,
            })
            lyric_elements[-1].append(lyric)

    return tree, systems, lyric_elements


def xml_reference(slots):
    return " ".join(s["text"] for s in slots if s["text"])


def strip_to_verse1(line: str) -> str:
    matches = list(VERSE1_MARKER_RE.finditer(line))
    plausible = [m for m in matches if m.start() <= max(35, len(line) // 3)]
    if plausible:
        return line[plausible[-1].end():].strip()

    m = LETTER_RE.search(line)
    return line[m.start():].strip() if m else ""


def line_score(line: str, reference: str):
    cleaned = strip_to_verse1(line)
    a = normalize_text(cleaned)
    b = normalize_text(reference)

    if not a:
        return -1.0

    similarity = SequenceMatcher(None, a, b).ratio() if b else 0.0
    letters = len(LETTER_RE.findall(cleaned))
    density = letters / max(1, len(cleaned))
    marker_bonus = 0.15 if VERSE1_MARKER_RE.search(line) else 0.0
    length_bonus = min(0.10, len(cleaned) / 800)

    return 0.68 * similarity + 0.14 * density + marker_bonus + length_bonus


def select_verse1_line(lines, reference):
    scored = sorted(
        ((line_score(line, reference), line) for line in lines),
        key=lambda x: x[0],
        reverse=True,
    )
    if not scored:
        return "", []
    return strip_to_verse1(scored[0][1]), scored


def clean_word_chunks(text: str):
    text = text.replace("–", "-").replace("—", "-").replace("_", "-")
    text = re.sub(
        fr"(?<=[{LATVIAN_LETTERS}])[,;:]+(?=[{LATVIAN_LETTERS}])",
        " ",
        text,
    )

    chunks = []
    for token in text.split():
        token = token.strip(' ,;:!?()[]{}“”„"«»|/\\')
        token = re.sub(
            fr"^[^{LATVIAN_LETTERS}'’._-]+|[^{LATVIAN_LETTERS}'’._-]+$",
            "",
            token,
        )
        if token and LETTER_RE.search(token):
            chunks.append(token)

    return chunks


def recover_with_pyphen(text: str):
    recovered, trace = [], []

    for chunk in clean_word_chunks(text):
        explicit_parts = [p for p in re.split(r"[-._]+", chunk) if p]

        if len(explicit_parts) > 1:
            for part in explicit_parts:
                cleaned = part.strip("'’")
                if cleaned:
                    recovered.append(cleaned)
                    trace.append((cleaned, "printed"))
            continue

        word = explicit_parts[0].strip("'’") if explicit_parts else ""
        if not word:
            continue

        inserted = HYPHENATOR.inserted(word)
        py_parts = [p for p in inserted.split("-") if p]

        if len(py_parts) > 1:
            for p in py_parts:
                recovered.append(p)
                trace.append((p, "pyphen"))
        else:
            recovered.append(word)
            trace.append((word, "unsplit"))

    return recovered, trace


def suspicious_token_reason(token: str):
    if not token:
        return "empty token"
    if any(ch.isdigit() for ch in token):
        return "contains digit"
    if re.search(r"[!@#$%^&*=+<>]", token):
        return "contains unusual punctuation"

    letters = [c for c in token if c.isalpha()]
    if letters and len(letters) <= 4 and sum(c.isupper() for c in letters) >= 2:
        common_ok = {"Ai", "Es", "Tu", "Ti", "Šu", "Kai", "Sai"}
        if token not in common_ok:
            return "short all/mostly-uppercase token"

    vowels = "aeiouāēīūōAEIOUĀĒĪŪŌ"
    if len(token) == 1 and token.isalpha() and token not in vowels:
        return "isolated consonant"

    return None


def assess_system_confidence(selected, trace, xml_slot_count):
    recovered = [t for t, _ in trace]
    reasons = []
    suspicious = []

    if not selected:
        return "LOW", ["no verse-1 OCR line selected"], []

    if len(recovered) != xml_slot_count:
        reasons.append(f"recovered {len(recovered)} units vs {xml_slot_count} XML slots")

    for token in recovered:
        why = suspicious_token_reason(token)
        if why:
            suspicious.append((token, why))

    if suspicious:
        reasons.append(
            "suspicious OCR token(s): "
            + ", ".join(f"{tok} ({why})" for tok, why in suspicious)
        )

    if reasons:
        return "LOW", reasons, suspicious

    n = max(1, len(trace))
    pyphen = sum(src == "pyphen" for _, src in trace)
    pyphen_ratio = pyphen / n

    if pyphen_ratio <= 0.20:
        return "HIGH", [f"exact match; {pyphen}/{n} units depend on Pyphen"], suspicious

    return "MEDIUM", [f"exact match; {pyphen}/{n} units depend on Pyphen"], suspicious


def syllabic_values_from_provenance(trace):
    """
    Conservative placeholder for syllabic labels.

    We preserve existing MusicXML <syllabic> values rather than rewriting them,
    because reconstructing begin/middle/end/single robustly from OCR token
    provenance deserves its own validation step.
    """
    return None


def replace_verse1_text(lyric_elements_by_system, recovered_by_system):
    """Replace ONLY <text> inside lyric number=1 elements."""
    for lyric_elements, recovered in zip(lyric_elements_by_system, recovered_by_system):
        if len(lyric_elements) != len(recovered):
            raise ValueError("Unsafe replacement attempted with mismatched counts")

        for lyric_el, new_text in zip(lyric_elements, recovered):
            text_el = lyric_el.find("text")
            if text_el is None:
                text_el = ET.SubElement(lyric_el, "text")
            text_el.text = new_text


def diagnose_and_maybe_correct(png_path, xml_path, corrected_dir, review_dir, debug_dir):
    gray = cv2.imread(str(png_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"Could not read image: {png_path}")

    detected_systems = detect_staff_systems(gray)
    tree, xml_systems, lyric_elements = read_xml_structure(xml_path)
    bands = make_lyric_bands(gray, detected_systems)

    details = []

    for i, (_, _, crop) in enumerate(bands):
        candidates, processed = ocr_lines(crop)
        slots = xml_systems[i] if i < len(xml_systems) else []
        selected, _ = select_verse1_line(candidates, xml_reference(slots))
        recovered, trace = recover_with_pyphen(selected)
        confidence, reasons, suspicious = assess_system_confidence(
            selected, trace, len(slots)
        )

        details.append({
            "system": i + 1,
            "selected": selected,
            "recovered": recovered,
            "trace": trace,
            "xml_slots": len(slots),
            "confidence": confidence,
            "reasons": reasons,
            "suspicious": suspicious,
        })

        debug_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(
            str(debug_dir / f"{png_path.stem}_system{i+1}_lyrics.png"),
            processed,
        )

    staff_match = len(detected_systems) == len(xml_systems)
    enough_systems = len(details) == len(xml_systems)
    supported_system_count = len(xml_systems) <= 4

    all_acceptable = (
        staff_match
        and enough_systems
        and supported_system_count
        and all(d["confidence"] in {"HIGH", "MEDIUM"} for d in details)
    )

    corrected_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)

    if all_acceptable:
        replace_verse1_text(
            lyric_elements,
            [d["recovered"] for d in details],
        )
        out_path = corrected_dir / xml_path.name
        try:
            ET.indent(tree, space="  ")
        except AttributeError:
            pass
        tree.write(out_path, encoding="UTF-8", xml_declaration=True)
        status = "CORRECTED"
    else:
        out_path = review_dir / xml_path.name
        shutil.copy2(xml_path, out_path)
        status = "REVIEW"

    return {
        "filename": png_path.stem,
        "status": status,
        "detected_systems": len(detected_systems),
        "xml_systems": len(xml_systems),
        "staff_match": staff_match,
        "details": details,
        "output_path": str(out_path),
        "supported_system_count": supported_system_count,
    }


def system_field(result, idx, key, default=""):
    for d in result["details"]:
        if d["system"] == idx:
            if key == "confidence":
                return d["confidence"]
            if key == "ocr_units":
                return len(d["recovered"])
            if key == "xml_slots":
                return d["xml_slots"]
            if key == "selected":
                return d["selected"]
            if key == "pyphen_units":
                return sum(src == "pyphen" for _, src in d["trace"])
            if key == "printed_units":
                return sum(src == "printed" for _, src in d["trace"])
            if key == "unsplit_units":
                return sum(src == "unsplit" for _, src in d["trace"])
            if key == "suspicious_tokens":
                return ", ".join(tok for tok, _ in d["suspicious"])
            if key == "assessment":
                return "; ".join(d["reasons"])
    return default


def write_report(results, report_path):
    fields = [
        "filename",
        "status",
        "detected_systems",
        "xml_systems",
        "staff_system_count_match",
        "output_path",
    ]

    for i in range(1, 5):
        fields += [
            f"system_{i}_confidence",
            f"system_{i}_ocr_units",
            f"system_{i}_xml_slots",
            f"system_{i}_printed_units",
            f"system_{i}_pyphen_units",
            f"system_{i}_unsplit_units",
            f"system_{i}_suspicious_tokens",
            f"system_{i}_assessment",
            f"system_{i}_selected_verse1",
        ]

    with report_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for r in results:
            row = {
                "filename": r["filename"],
                "status": r["status"],
                "detected_systems": r["detected_systems"],
                "xml_systems": r["xml_systems"],
                "staff_system_count_match": r["staff_match"],
                "output_path": r["output_path"],
            }

            for i in range(1, 5):
                row.update({
                    f"system_{i}_confidence": system_field(r, i, "confidence"),
                    f"system_{i}_ocr_units": system_field(r, i, "ocr_units"),
                    f"system_{i}_xml_slots": system_field(r, i, "xml_slots"),
                    f"system_{i}_printed_units": system_field(r, i, "printed_units"),
                    f"system_{i}_pyphen_units": system_field(r, i, "pyphen_units"),
                    f"system_{i}_unsplit_units": system_field(r, i, "unsplit_units"),
                    f"system_{i}_suspicious_tokens": system_field(r, i, "suspicious_tokens"),
                    f"system_{i}_assessment": system_field(r, i, "assessment"),
                    f"system_{i}_selected_verse1": system_field(r, i, "selected"),
                })

            writer.writerow(row)


def pair_folders(png_dir, xml_dir):
    pairs = []
    for png in sorted(png_dir.glob("*.png")):
        xml = xml_dir / f"{png.stem}.xml"
        if xml.exists():
            pairs.append((png, xml))
        else:
            print(f"WARNING: no XML partner for {png.name}", file=sys.stderr)
    return pairs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--png-dir", required=True, type=Path)
    parser.add_argument("--xml-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    corrected_dir = args.output_dir / "corrected"
    review_dir = args.output_dir / "review"
    debug_dir = args.output_dir / "debug"
    report_path = args.output_dir / "correction_report.csv"

    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = []

    for png, xml in pair_folders(args.png_dir, args.xml_dir):
        try:
            result = diagnose_and_maybe_correct(
                png, xml, corrected_dir, review_dir, debug_dir
            )
            results.append(result)
            print(
                f"{result['filename']}: {result['status']} "
                f"({result['xml_systems']} system(s))"
            )
        except Exception as exc:
            print(f"ERROR: {png.name}: {exc}", file=sys.stderr)

    write_report(results, report_path)

    corrected_count = sum(r["status"] == "CORRECTED" for r in results)
    review_count = sum(r["status"] == "REVIEW" for r in results)

    print("\nDone.")
    print(f"Corrected: {corrected_count}")
    print(f"Review:    {review_count}")
    print(f"Report:    {report_path}")


if __name__ == "__main__":
    main()

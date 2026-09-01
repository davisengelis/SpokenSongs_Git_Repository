#!/usr/bin/env python3
"""
lyrics_ocr_correction_prototype_v2.py

Second conservative/assisted correction prototype.

Main changes from v1:
  1. Better LEFT-EDGE anchoring of verse 1:
       - prefers a detected "1." verse marker;
       - otherwise uses the bounding box of the selected lyric line;
       - this is designed to exclude pitch labels such as la¹ / reb² under the clef.
  2. Three output categories:
       corrected/  = all systems HIGH confidence
       check/      = structurally aligned, but at least one system MEDIUM
       review/     = count mismatch, suspicious OCR, staff mismatch, or other unsafe case
  3. CSV report retained and expanded through system_4.
  4. Optional one-note XML anchor repair:
       - if verse 1 begins on the SECOND pitched note in MusicXML,
       - and OCR alignment is otherwise exact,
       - the script can shift the verse-1 lyric elements one note earlier.
       - such files always go to check/, never corrected/.
  5. Verse 2+ is ignored for confidence and left untouched.

Original input XML files are NEVER modified.

Requirements:
    pip install pytesseract opencv-python pyphen
    Tesseract with Latvian ("lav") trained data.

Run:
    python lyrics_ocr_correction_prototype_v2.py \
        --png-dir input/png \
        --xml-dir input/xml \
        --output-dir output_v2
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
from pytesseract import Output


LATVIAN_LETTERS = "A-Za-zĀāČčĒēĢģĪīĶķĻļŅņŠšŪūŽžŌō"
LETTER_RE = re.compile(fr"[{LATVIAN_LETTERS}]")
VERSE1_MARKER_RE = re.compile(r"(?<!\d)(?:1|I|l)\s*[.,]\s*", re.UNICODE)
HYPHENATOR = pyphen.Pyphen(lang="lv_LV")


def normalize_text(s: str) -> str:
    s = s.lower().replace("_", "-").replace("–", "-").replace("—", "-")
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


def data_lines(processed, psm=6):
    """
    OCR with coordinates and group words into lines.
    Returns list of dicts: text, x1, y1, x2, y2.
    """
    data = pytesseract.image_to_data(
        processed,
        lang="lav",
        config=f"--psm {psm} -c preserve_interword_spaces=1",
        output_type=Output.DICT,
    )

    grouped = {}
    n = len(data["text"])

    for i in range(n):
        txt = (data["text"][i] or "").strip()
        if not txt:
            continue

        key = (
            data["block_num"][i],
            data["par_num"][i],
            data["line_num"][i],
        )
        x, y = data["left"][i], data["top"][i]
        w, h = data["width"][i], data["height"][i]

        g = grouped.setdefault(
            key,
            {"words": [], "x1": x, "y1": y, "x2": x+w, "y2": y+h}
        )
        g["words"].append((txt, x, y, w, h))
        g["x1"] = min(g["x1"], x)
        g["y1"] = min(g["y1"], y)
        g["x2"] = max(g["x2"], x+w)
        g["y2"] = max(g["y2"], y+h)

    lines = []
    for g in grouped.values():
        words = sorted(g["words"], key=lambda z: z[1])
        text = " ".join(w[0] for w in words)
        lines.append({
            "text": text,
            "x1": g["x1"],
            "y1": g["y1"],
            "x2": g["x2"],
            "y2": g["y2"],
            "words": words,
        })

    lines.sort(key=lambda d: (d["y1"], d["x1"]))
    return lines


def strip_to_verse1(line: str) -> str:
    matches = list(VERSE1_MARKER_RE.finditer(line))
    plausible = [m for m in matches if m.start() <= max(35, len(line)//3)]
    if plausible:
        return line[plausible[-1].end():].strip()

    m = LETTER_RE.search(line)
    return line[m.start():].strip() if m else ""


def line_score(line: str, reference: str):
    cleaned = strip_to_verse1(line)
    a, b = normalize_text(cleaned), normalize_text(reference)

    if not a:
        return -1.0

    similarity = SequenceMatcher(None, a, b).ratio() if b else 0.0
    letters = len(LETTER_RE.findall(cleaned))
    density = letters / max(1, len(cleaned))
    marker_bonus = 0.15 if VERSE1_MARKER_RE.search(line) else 0.0
    length_bonus = min(0.10, len(cleaned) / 800)

    return 0.68*similarity + 0.14*density + marker_bonus + length_bonus


def xml_reference(slots):
    return " ".join(s["text"] for s in slots if s["text"])


def select_line_with_bbox(processed, reference):
    """
    Select verse-1 OCR line with coordinates.

    Critical v2 behavior:
    if a word resembling "1." is found on the selected line, the lyric start
    is anchored immediately AFTER that marker, so pitch labels to its left
    (la1, reb2, etc.) are excluded.
    """
    candidates = []

    for psm in (3, 6, 11, 12):
        for line in data_lines(processed, psm=psm):
            sc = line_score(line["text"], reference)
            candidates.append((sc, line))

    if not candidates:
        return "", None, "none", False

    candidates.sort(key=lambda z: z[0], reverse=True)
    best = candidates[0][1]
    words = best["words"]

    anchor_x = best["x1"]
    anchor_type = "line_bbox"
    pitch_label_removed = False

    # Search early words for verse marker variants.
    for idx, (txt, x, y, w, h) in enumerate(words[:5]):
        t = txt.replace(" ", "")
        if re.fullmatch(r"(?:1|I|l)[\.,]?", t) or t.startswith("1."):
            anchor_x = x + w + 2
            anchor_type = "verse_marker"
            pitch_label_removed = anchor_x > best["x1"] + 5
            break

        # Sometimes OCR joins marker and first syllable: "1.Māmiņ"
        m = re.match(r"^(?:1|I|l)[\.,](.+)$", t)
        if m:
            anchor_x = x
            anchor_type = "joined_verse_marker"
            pitch_label_removed = anchor_x > best["x1"] + 5
            break

    # Rebuild selected text using only words at/after anchor.
    kept = []
    for txt, x, y, w, h in words:
        if x + w >= anchor_x:
            kept.append(txt)

    selected = strip_to_verse1(" ".join(kept))
    return selected, anchor_x, anchor_type, pitch_label_removed


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


def assess_system(selected, trace, xml_slot_count):
    recovered = [t for t, _ in trace]
    reasons, suspicious = [], []

    if not selected:
        return "LOW", ["no verse-1 line selected"], []

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
    ratio = pyphen / n

    if ratio <= 0.20:
        return "HIGH", [f"exact match; {pyphen}/{n} units depend on Pyphen"], suspicious

    return "MEDIUM", [f"exact match; {pyphen}/{n} units depend on Pyphen"], suspicious


def read_xml_structure(xml_path: Path):
    """
    Keep:
      - system-level verse-1 lyric elements
      - system-level full note list
    so we can optionally repair a one-note initial displacement.
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    systems = [[]]
    lyric_elements = [[]]
    notes_by_system = [[]]

    for measure in root.findall(".//measure"):
        print_el = measure.find("print")
        if print_el is not None and print_el.get("new-system") == "yes" and notes_by_system[-1]:
            systems.append([])
            lyric_elements.append([])
            notes_by_system.append([])

        for note in measure.findall("note"):
            notes_by_system[-1].append(note)

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

    return tree, systems, lyric_elements, notes_by_system


def is_pitched_note(note):
    return note.find("pitch") is not None and note.find("rest") is None


def find_first_lyric_note_index(notes):
    for i, note in enumerate(notes):
        for lyr in note.findall("lyric"):
            if lyr.get("number") == "1":
                return i
    return None


def move_all_verse1_lyrics_one_note_left(notes):
    """
    Conservative one-note repair:
      if current verse 1 starts on note index 1, move each verse-1 lyric element
      one note earlier, preserving relative gaps.

    Returns True if applied.
    """
    current_indices = []
    lyric_nodes = []

    for i, note in enumerate(notes):
        for lyr in list(note.findall("lyric")):
            if lyr.get("number") == "1":
                current_indices.append(i)
                lyric_nodes.append((note, lyr))

    if not current_indices or current_indices[0] != 1:
        return False

    new_indices = [i - 1 for i in current_indices]
    if min(new_indices) < 0 or max(new_indices) >= len(notes):
        return False

    if not is_pitched_note(notes[0]):
        return False

    # Remove first, then reattach to preserve one lyric-1 element per destination.
    for parent, lyr in lyric_nodes:
        parent.remove(lyr)

    for (_, lyr), ni in zip(lyric_nodes, new_indices):
        notes[ni].append(lyr)

    return True


def replace_verse1_text(lyric_elements_by_system, recovered_by_system):
    for lyric_elements, recovered in zip(lyric_elements_by_system, recovered_by_system):
        if len(lyric_elements) != len(recovered):
            raise ValueError("Unsafe replacement attempted with mismatched counts")

        for lyric_el, new_text in zip(lyric_elements, recovered):
            text_el = lyric_el.find("text")
            if text_el is None:
                text_el = ET.SubElement(lyric_el, "text")
            text_el.text = new_text


def diagnose_and_write(png_path, xml_path, corrected_dir, check_dir, review_dir, debug_dir):
    gray = cv2.imread(str(png_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"Could not read image: {png_path}")

    detected_systems = detect_staff_systems(gray)
    tree, xml_systems, lyric_elements, notes_by_system = read_xml_structure(xml_path)
    bands = make_lyric_bands(gray, detected_systems)

    details = []

    for i, (_, _, crop) in enumerate(bands):
        processed = preprocess_for_ocr(crop)
        slots = xml_systems[i] if i < len(xml_systems) else []

        selected, anchor_x, anchor_type, pitch_removed = select_line_with_bbox(
            processed, xml_reference(slots)
        )
        recovered, trace = recover_with_pyphen(selected)
        confidence, reasons, suspicious = assess_system(
            selected, trace, len(slots)
        )

        first_lyric_note_idx = (
            find_first_lyric_note_index(notes_by_system[i])
            if i < len(notes_by_system) else None
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
            "anchor_x": anchor_x,
            "anchor_type": anchor_type,
            "possible_pitch_label_removed": pitch_removed,
            "first_lyric_note_index": first_lyric_note_idx,
            "one_note_anchor_repair": False,
        })

        debug_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(
            str(debug_dir / f"{png_path.stem}_system{i+1}_lyrics.png"),
            processed,
        )

    staff_match = len(detected_systems) == len(xml_systems)
    exact_system_count = len(details) == len(xml_systems)
    supported = len(xml_systems) <= 4

    # Safe textual alignment means every system has an exact OCR/XML slot count
    # and no suspicious OCR token.
    structurally_aligned = (
        staff_match
        and exact_system_count
        and supported
        and all(d["confidence"] in {"HIGH", "MEDIUM"} for d in details)
    )

    anchor_repair_applied = False

    if structurally_aligned:
        # Optional one-note shift only when XML verse 1 starts on note 2.
        for i, d in enumerate(details):
            if d["first_lyric_note_index"] == 1:
                applied = move_all_verse1_lyrics_one_note_left(notes_by_system[i])
                d["one_note_anchor_repair"] = applied
                anchor_repair_applied = anchor_repair_applied or applied

        # Important: lyric_elements still point to the same lyric nodes even
        # after those nodes move to different note parents.
        replace_verse1_text(
            lyric_elements,
            [d["recovered"] for d in details],
        )

        all_high = all(d["confidence"] == "HIGH" for d in details)

        if all_high and not anchor_repair_applied:
            category = "CORRECTED"
            out_dir = corrected_dir
        else:
            category = "CHECK"
            out_dir = check_dir

        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / xml_path.name
        try:
            ET.indent(tree, space="  ")
        except AttributeError:
            pass
        tree.write(out_path, encoding="UTF-8", xml_declaration=True)

    else:
        category = "REVIEW"
        review_dir.mkdir(parents=True, exist_ok=True)
        out_path = review_dir / xml_path.name
        shutil.copy2(xml_path, out_path)

    return {
        "filename": png_path.stem,
        "status": category,
        "detected_systems": len(detected_systems),
        "xml_systems": len(xml_systems),
        "staff_match": staff_match,
        "details": details,
        "anchor_repair_applied": anchor_repair_applied,
        "output_path": str(out_path),
    }


def system_field(result, idx, key, default=""):
    for d in result["details"]:
        if d["system"] == idx:
            if key in d:
                return d[key]
            if key == "ocr_units":
                return len(d["recovered"])
            if key == "printed_units":
                return sum(src == "printed" for _, src in d["trace"])
            if key == "pyphen_units":
                return sum(src == "pyphen" for _, src in d["trace"])
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
        "anchor_repair_applied",
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
            f"system_{i}_anchor_type",
            f"system_{i}_anchor_x",
            f"system_{i}_possible_pitch_label_removed",
            f"system_{i}_first_lyric_note_index",
            f"system_{i}_one_note_anchor_repair",
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
                "anchor_repair_applied": r["anchor_repair_applied"],
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
                    f"system_{i}_anchor_type": system_field(r, i, "anchor_type"),
                    f"system_{i}_anchor_x": system_field(r, i, "anchor_x"),
                    f"system_{i}_possible_pitch_label_removed": system_field(r, i, "possible_pitch_label_removed"),
                    f"system_{i}_first_lyric_note_index": system_field(r, i, "first_lyric_note_index"),
                    f"system_{i}_one_note_anchor_repair": system_field(r, i, "one_note_anchor_repair"),
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
    check_dir = args.output_dir / "check"
    review_dir = args.output_dir / "review"
    debug_dir = args.output_dir / "debug"
    report_path = args.output_dir / "correction_report_v2.csv"

    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = []

    for png, xml in pair_folders(args.png_dir, args.xml_dir):
        try:
            r = diagnose_and_write(
                png, xml, corrected_dir, check_dir, review_dir, debug_dir
            )
            results.append(r)
            print(
                f"{r['filename']}: {r['status']} "
                f"({r['xml_systems']} system(s))"
            )
        except Exception as exc:
            print(f"ERROR: {png.name}: {exc}", file=sys.stderr)

    write_report(results, report_path)

    counts = {k: sum(r["status"] == k for r in results)
              for k in ("CORRECTED", "CHECK", "REVIEW")}

    print("\nDone.")
    print(f"Corrected: {counts['CORRECTED']}")
    print(f"Check:     {counts['CHECK']}")
    print(f"Review:    {counts['REVIEW']}")
    print(f"Report:    {report_path}")


if __name__ == "__main__":
    main()

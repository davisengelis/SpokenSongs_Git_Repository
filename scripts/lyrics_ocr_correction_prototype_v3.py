#!/usr/bin/env python3
"""
lyrics_ocr_correction_prototype_v3.py

Gap-aware OCR/MusicXML correction prototype.

Main idea:
  * OCR quality and syllable PLACEMENT quality are assessed separately.
  * OCR syllable count no longer has to equal the MusicXML lyric-slot count.
  * A global sequence alignment maps OCR units to existing verse-1 lyric slots.
  * Missing OCR units become localized gaps instead of shifting/rejecting the song.
  * Matched XML slots are corrected; unmatched XML slots are left unchanged.
  * Extra OCR units are reported but are NOT inserted automatically.

Output:
  corrected/  very strong alignment + strong text confidence + no gaps
  check/      useful, localized alignment with some OCR/gap uncertainty
  review/     alignment itself is too uncertain
  debug/      cropped lyric strips
  correction_report_v3.csv

Original XML files are NEVER modified.

Requirements:
    pip install pytesseract opencv-python pyphen
    Tesseract with Latvian traineddata: lav

Run:
    python lyrics_ocr_correction_prototype_v3.py \
        --png-dir input/png \
        --xml-dir input/xml \
        --output-dir output_v3
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


# -------------------------- basic text helpers --------------------------

def normalize_text(s: str) -> str:
    s = (s or "").lower().replace("_", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(fr"[^{LATVIAN_LETTERS}'’ -]", "", s)
    return s.strip()


def token_similarity(a: str, b: str) -> float:
    a, b = normalize_text(a), normalize_text(b)
    if not a and not b:
        return 1.0
    if not a or not b:
        # Empty Soundslice OCR is not evidence against an OCR token.
        return 0.42
    return SequenceMatcher(None, a, b).ratio()


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


# -------------------------- staff / lyric regions --------------------------

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


# -------------------------- OCR line selection --------------------------

def data_lines(processed, psm=6):
    data = pytesseract.image_to_data(
        processed,
        lang="lav",
        config=f"--psm {psm} -c preserve_interword_spaces=1",
        output_type=Output.DICT,
    )

    grouped = {}
    for i, txt in enumerate(data["text"]):
        txt = (txt or "").strip()
        if not txt:
            continue

        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        x, y = data["left"][i], data["top"][i]
        w, h = data["width"][i], data["height"][i]

        g = grouped.setdefault(
            key, {"words": [], "x1": x, "y1": y, "x2": x + w, "y2": y + h}
        )
        g["words"].append((txt, x, y, w, h))
        g["x1"] = min(g["x1"], x)
        g["y1"] = min(g["y1"], y)
        g["x2"] = max(g["x2"], x + w)
        g["y2"] = max(g["y2"], y + h)

    lines = []
    for g in grouped.values():
        words = sorted(g["words"], key=lambda z: z[1])
        lines.append({
            "text": " ".join(w[0] for w in words),
            "x1": g["x1"], "y1": g["y1"], "x2": g["x2"], "y2": g["y2"],
            "words": words,
        })

    return sorted(lines, key=lambda d: (d["y1"], d["x1"]))


def strip_to_verse1(line: str) -> str:
    matches = list(VERSE1_MARKER_RE.finditer(line))
    plausible = [m for m in matches if m.start() <= max(35, len(line) // 3)]
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
    marker_bonus = 0.16 if VERSE1_MARKER_RE.search(line) else 0.0
    length_bonus = min(0.10, len(cleaned) / 800)
    return 0.67 * similarity + 0.15 * density + marker_bonus + length_bonus


def select_line_with_bbox(processed, reference):
    candidates = []
    for psm in (3, 6, 11, 12):
        for line in data_lines(processed, psm):
            candidates.append((line_score(line["text"], reference), line))

    if not candidates:
        return "", None, "none", False

    candidates.sort(key=lambda z: z[0], reverse=True)
    best = candidates[0][1]
    words = best["words"]

    anchor_x = best["x1"]
    anchor_type = "line_bbox"
    pitch_label_removed = False

    for txt, x, y, w, h in words[:6]:
        t = txt.replace(" ", "")

        if re.fullmatch(r"(?:1|I|l)[\.,]?", t):
            anchor_x = x + w + 2
            anchor_type = "verse_marker"
            pitch_label_removed = anchor_x > best["x1"] + 5
            break

        if re.match(r"^(?:1|I|l)[\.,].+", t):
            anchor_x = x
            anchor_type = "joined_verse_marker"
            pitch_label_removed = anchor_x > best["x1"] + 5
            break

    kept = [txt for txt, x, y, w, h in words if x + w >= anchor_x]
    return strip_to_verse1(" ".join(kept)), anchor_x, anchor_type, pitch_label_removed


# -------------------------- syllable recovery --------------------------

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


# -------------------------- MusicXML --------------------------

def read_xml_structure(xml_path: Path):
    tree = ET.parse(xml_path)
    root = tree.getroot()

    slots_by_system = [[]]
    lyric_elements_by_system = [[]]

    for measure in root.findall(".//measure"):
        print_el = measure.find("print")
        if (
            print_el is not None
            and print_el.get("new-system") == "yes"
            and slots_by_system[-1]
        ):
            slots_by_system.append([])
            lyric_elements_by_system.append([])

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
            slots_by_system[-1].append({
                "text": "" if text_el is None or text_el.text is None else text_el.text,
                "syllabic": "" if syllabic_el is None or syllabic_el.text is None else syllabic_el.text,
            })
            lyric_elements_by_system[-1].append(lyric)

    return tree, slots_by_system, lyric_elements_by_system


def xml_reference(slots):
    return " ".join(s["text"] for s in slots if s["text"])


# -------------------------- gap-aware alignment --------------------------

def align_ocr_to_xml(ocr_tokens, xml_slots):
    """
    Needleman-Wunsch-style global alignment.

    Returns a list of pairs:
       (ocr_index or None, xml_index or None, pair_score)

    Missing OCR token => (None, xml_index, ...)
    Extra OCR token   => (ocr_index, None, ...)
    """
    n, m = len(ocr_tokens), len(xml_slots)

    # Gap costs deliberately moderate: a single lost OCR syllable should
    # localize as one gap rather than make the whole system fail.
    gap_ocr = -0.72   # XML slot has no OCR token
    gap_xml = -0.82   # OCR token has no XML slot

    dp = np.full((n + 1, m + 1), -1e9, dtype=float)
    back = np.empty((n + 1, m + 1), dtype=object)
    dp[0, 0] = 0.0

    for i in range(1, n + 1):
        dp[i, 0] = dp[i - 1, 0] + gap_xml
        back[i, 0] = "U"
    for j in range(1, m + 1):
        dp[0, j] = dp[0, j - 1] + gap_ocr
        back[0, j] = "L"

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sim = token_similarity(ocr_tokens[i - 1], xml_slots[j - 1]["text"])

            # Similarity is weak textual evidence only. Order does most work.
            match_score = -0.15 + 1.15 * sim

            # Mild diagonal-position prior reduces unnecessary drifting.
            if n > 1 and m > 1:
                rel_i = (i - 1) / (n - 1)
                rel_j = (j - 1) / (m - 1)
                match_score -= 0.35 * abs(rel_i - rel_j)

            diag = dp[i - 1, j - 1] + match_score
            up = dp[i - 1, j] + gap_xml
            left = dp[i, j - 1] + gap_ocr

            best = max(diag, up, left)
            dp[i, j] = best
            back[i, j] = "D" if best == diag else ("U" if best == up else "L")

    alignment = []
    i, j = n, m

    while i > 0 or j > 0:
        move = back[i, j]
        if move == "D":
            sim = token_similarity(ocr_tokens[i - 1], xml_slots[j - 1]["text"])
            alignment.append((i - 1, j - 1, sim))
            i -= 1
            j -= 1
        elif move == "U":
            alignment.append((i - 1, None, 0.0))
            i -= 1
        else:
            alignment.append((None, j - 1, 0.0))
            j -= 1

    alignment.reverse()
    return alignment


def assess_alignment(ocr_tokens, xml_slots, alignment):
    matched = [(oi, xi, sc) for oi, xi, sc in alignment if oi is not None and xi is not None]
    missing_xml = [xi for oi, xi, _ in alignment if oi is None and xi is not None]
    extra_ocr = [oi for oi, xi, _ in alignment if oi is not None and xi is None]

    total_slots = max(1, len(xml_slots))
    gap_count = len(missing_xml) + len(extra_ocr)
    gap_rate = gap_count / total_slots

    # Monotonicity is guaranteed by the DP. Evaluate coverage and textual support.
    matched_rate = len(matched) / total_slots
    avg_similarity = (
        sum(sc for _, _, sc in matched) / len(matched) if matched else 0.0
    )

    # Alignment confidence is intentionally tolerant of a few OCR omissions.
    if matched_rate >= 0.90 and gap_rate <= 0.12:
        alignment_conf = "HIGH"
    elif matched_rate >= 0.72 and gap_rate <= 0.30:
        alignment_conf = "MEDIUM"
    else:
        alignment_conf = "LOW"

    return {
        "confidence": alignment_conf,
        "matched_slots": len(matched),
        "missing_xml_slots": missing_xml,
        "extra_ocr_tokens": extra_ocr,
        "gap_rate": gap_rate,
        "average_similarity": avg_similarity,
    }


def assess_text(trace):
    suspicious = []
    for token, _ in trace:
        why = suspicious_token_reason(token)
        if why:
            suspicious.append((token, why))

    n = max(1, len(trace))
    pyphen = sum(src == "pyphen" for _, src in trace)
    suspicious_rate = len(suspicious) / n
    pyphen_rate = pyphen / n

    if suspicious_rate == 0 and pyphen_rate <= 0.20:
        conf = "HIGH"
    elif suspicious_rate <= 0.12 and pyphen_rate <= 0.45:
        conf = "MEDIUM"
    else:
        conf = "LOW"

    return {
        "confidence": conf,
        "suspicious": suspicious,
        "printed_units": sum(src == "printed" for _, src in trace),
        "pyphen_units": pyphen,
        "unsplit_units": sum(src == "unsplit" for _, src in trace),
    }


def patch_matched_slots(lyric_elements, ocr_tokens, alignment):
    """
    Replace text ONLY on OCR<->XML matched pairs.
    XML slots corresponding to missing OCR syllables stay unchanged.
    Extra OCR tokens are not inserted.
    """
    replaced = 0
    for oi, xi, _ in alignment:
        if oi is None or xi is None:
            continue
        lyric_el = lyric_elements[xi]
        text_el = lyric_el.find("text")
        if text_el is None:
            text_el = ET.SubElement(lyric_el, "text")
        text_el.text = ocr_tokens[oi]
        replaced += 1
    return replaced


# -------------------------- per-file workflow --------------------------

def classify_file(system_details, staff_match):
    if not staff_match or not system_details:
        return "REVIEW"

    alignment_levels = [d["alignment_confidence"] for d in system_details]
    text_levels = [d["text_confidence"] for d in system_details]
    total_gaps = sum(d["missing_count"] + d["extra_count"] for d in system_details)

    if (
        all(a == "HIGH" for a in alignment_levels)
        and all(t == "HIGH" for t in text_levels)
        and total_gaps == 0
    ):
        return "CORRECTED"

    if all(a in {"HIGH", "MEDIUM"} for a in alignment_levels):
        return "CHECK"

    return "REVIEW"


def process_pair(png_path, xml_path, corrected_dir, check_dir, review_dir, debug_dir):
    gray = cv2.imread(str(png_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"Could not read image: {png_path}")

    systems = detect_staff_systems(gray)
    bands = make_lyric_bands(gray, systems)
    tree, xml_systems, lyric_elements_by_system = read_xml_structure(xml_path)

    staff_match = len(systems) == len(xml_systems)
    details = []

    for i, (_, _, crop) in enumerate(bands):
        processed = preprocess_for_ocr(crop)
        slots = xml_systems[i] if i < len(xml_systems) else []

        selected, anchor_x, anchor_type, pitch_removed = select_line_with_bbox(
            processed, xml_reference(slots)
        )
        tokens, trace = recover_with_pyphen(selected)

        alignment = align_ocr_to_xml(tokens, slots)
        a = assess_alignment(tokens, slots, alignment)
        t = assess_text(trace)

        details.append({
            "system": i + 1,
            "selected": selected,
            "tokens": tokens,
            "trace": trace,
            "alignment": alignment,
            "alignment_confidence": a["confidence"],
            "text_confidence": t["confidence"],
            "matched_count": a["matched_slots"],
            "missing_count": len(a["missing_xml_slots"]),
            "extra_count": len(a["extra_ocr_tokens"]),
            "missing_xml_slots": a["missing_xml_slots"],
            "extra_ocr_tokens": a["extra_ocr_tokens"],
            "gap_rate": a["gap_rate"],
            "average_similarity": a["average_similarity"],
            "suspicious": t["suspicious"],
            "printed_units": t["printed_units"],
            "pyphen_units": t["pyphen_units"],
            "unsplit_units": t["unsplit_units"],
            "xml_slots": len(slots),
            "anchor_x": anchor_x,
            "anchor_type": anchor_type,
            "possible_pitch_label_removed": pitch_removed,
        })

        debug_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(
            str(debug_dir / f"{png_path.stem}_system{i+1}_lyrics.png"),
            processed,
        )

    category = classify_file(details, staff_match)

    if category in {"CORRECTED", "CHECK"}:
        for i, d in enumerate(details):
            if i >= len(lyric_elements_by_system):
                continue
            patch_matched_slots(
                lyric_elements_by_system[i],
                d["tokens"],
                d["alignment"],
            )

        out_dir = corrected_dir if category == "CORRECTED" else check_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / xml_path.name
        try:
            ET.indent(tree, space="  ")
        except AttributeError:
            pass
        tree.write(out_path, encoding="UTF-8", xml_declaration=True)

    else:
        review_dir.mkdir(parents=True, exist_ok=True)
        out_path = review_dir / xml_path.name
        shutil.copy2(xml_path, out_path)

    return {
        "filename": png_path.stem,
        "status": category,
        "detected_systems": len(systems),
        "xml_systems": len(xml_systems),
        "staff_match": staff_match,
        "details": details,
        "output_path": str(out_path),
    }


# -------------------------- CSV --------------------------

def system_detail(result, idx):
    for d in result["details"]:
        if d["system"] == idx:
            return d
    return None


def write_report(results, report_path):
    fields = [
        "filename", "status", "detected_systems", "xml_systems",
        "staff_system_count_match", "output_path",
    ]

    for i in range(1, 5):
        fields += [
            f"system_{i}_alignment_confidence",
            f"system_{i}_text_confidence",
            f"system_{i}_ocr_units",
            f"system_{i}_xml_slots",
            f"system_{i}_matched_slots",
            f"system_{i}_missing_xml_slots",
            f"system_{i}_extra_ocr_tokens",
            f"system_{i}_gap_rate",
            f"system_{i}_average_text_similarity",
            f"system_{i}_printed_units",
            f"system_{i}_pyphen_units",
            f"system_{i}_unsplit_units",
            f"system_{i}_suspicious_tokens",
            f"system_{i}_selected_verse1",
            f"system_{i}_anchor_type",
            f"system_{i}_anchor_x",
            f"system_{i}_possible_pitch_label_removed",
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
                d = system_detail(r, i)
                if d is None:
                    for name in fields:
                        if name.startswith(f"system_{i}_"):
                            row[name] = ""
                    continue

                row.update({
                    f"system_{i}_alignment_confidence": d["alignment_confidence"],
                    f"system_{i}_text_confidence": d["text_confidence"],
                    f"system_{i}_ocr_units": len(d["tokens"]),
                    f"system_{i}_xml_slots": d["xml_slots"],
                    f"system_{i}_matched_slots": d["matched_count"],
                    f"system_{i}_missing_xml_slots": ",".join(
                        str(x + 1) for x in d["missing_xml_slots"]
                    ),
                    f"system_{i}_extra_ocr_tokens": ",".join(
                        d["tokens"][x] for x in d["extra_ocr_tokens"]
                        if 0 <= x < len(d["tokens"])
                    ),
                    f"system_{i}_gap_rate": round(d["gap_rate"], 3),
                    f"system_{i}_average_text_similarity": round(
                        d["average_similarity"], 3
                    ),
                    f"system_{i}_printed_units": d["printed_units"],
                    f"system_{i}_pyphen_units": d["pyphen_units"],
                    f"system_{i}_unsplit_units": d["unsplit_units"],
                    f"system_{i}_suspicious_tokens": ",".join(
                        tok for tok, _ in d["suspicious"]
                    ),
                    f"system_{i}_selected_verse1": d["selected"],
                    f"system_{i}_anchor_type": d["anchor_type"],
                    f"system_{i}_anchor_x": d["anchor_x"],
                    f"system_{i}_possible_pitch_label_removed":
                        d["possible_pitch_label_removed"],
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
    report_path = args.output_dir / "correction_report_v3.csv"

    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for png, xml in pair_folders(args.png_dir, args.xml_dir):
        try:
            result = process_pair(
                png, xml, corrected_dir, check_dir, review_dir, debug_dir
            )
            results.append(result)
            print(
                f"{result['filename']}: {result['status']} "
                f"({result['xml_systems']} system(s))"
            )
        except Exception as exc:
            print(f"ERROR: {png.name}: {exc}", file=sys.stderr)

    write_report(results, report_path)

    counts = {
        k: sum(r["status"] == k for r in results)
        for k in ("CORRECTED", "CHECK", "REVIEW")
    }

    print("\nDone.")
    print(f"Corrected: {counts['CORRECTED']}")
    print(f"Check:     {counts['CHECK']}")
    print(f"Review:    {counts['REVIEW']}")
    print(f"Report:    {report_path}")


if __name__ == "__main__":
    main()

# Spoken Songs

A project led by Ieva Vīvere and realised at LU LFMI.

## Folder structure

- data/raw/ — original folders received from colleague
- data/extracted/metadata/ — metadata images extracted from raw folders
- data/ocr/ — OCR text outputs
- metadata/master.csv — main metadata table
- data/mei/raw/ — MEI files exported from MuseScore
- data/mei/enriched/ — MEI files after metadata insertion
- scripts/ — reusable Bash and Python scripts

## Workflow

1. Place original data-unit folders in data/raw/
2. Run metadata image extraction script
3. Run OCR script
4. Correct OCR text manually
5. Export MEI from MuseScore
6. Run MEI metadata enrichment script
7. Check final MEI in MEI-friend

## Commands

```bash
bash scripts/collect_metadata.sh data/raw data/extracted/metadata
bash scripts/collect_text.sh data/raw data/extracted/text


bash scripts/extract_metadata_images.sh data/raw data/extracted/metadata
python scripts/ocr_images.py data/extracted/metadata data/ocr
python scripts/enrich_mei.py data/mei/raw metadata/master.csv data/mei/enriched
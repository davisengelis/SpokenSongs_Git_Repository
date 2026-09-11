Spoken Songs MEI Research Viewer
================================

This version combines:

1. the enriched MEI corpus;
2. Verovio witness switching for <app>/<rdg>;
3. the three CSV outputs from pilot_variant_analysis_music21.py.

Files to load
-------------
First choose the enriched folder:

/Users/davisengelis/SpokenSongs_CodeBook/data/mei/enriched

Then choose these three analysis CSV files:

pilot_song_summary.csv
pilot_pairwise_summary.csv
pilot_measure_comparisons.csv

They may be in any folder. Select all three at once in the file picker.

What is shown
-------------
Corpus index:
- VitolinsSource
- title
- available witnesses
- analysis badges (variant %, pitch/rhythm/text counts)

Song view:
- Verovio notation
- A/B/C witness switching
- song-level summary cards
- pairwise witness summary
- measure-by-measure comparison table

Measure interaction:
Click a measure row in the analysis table. The viewer looks up the xml:id of
that numbered measure in the currently selected <rdg> and highlights the
corresponding rendered Verovio measure.

Run on macOS
------------
1. Unzip the viewer.
2. In Terminal:

   cd ~/Downloads/verovio_corpus_research_viewer
   python3 -m http.server 8000

3. Open:

   http://localhost:8000

If port 8000 is already used by the older viewer, either stop it with Control-C,
or use another port:

   python3 -m http.server 8001

and open:

   http://localhost:8001

Notes
-----
- MEI and CSV files are read locally in the browser.
- The browser needs internet access to load Verovio from verovio.org.
- Analysis is optional; the corpus viewer still works without the CSVs.
- The analysis joins to MEI by numeric source_number (V2-####).

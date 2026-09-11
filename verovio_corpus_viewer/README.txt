Spoken Songs MEI Corpus Viewer
==============================

Purpose
-------
This browser viewer lets you select the entire data/mei/enriched folder.
It builds a local corpus index from the merged MEI files and lets you:

- browse by VitolinsSource and work title;
- search by source number, title, or filename;
- filter to songs that contain variant readings;
- render ordinary single-reading MEI files;
- automatically discover <rdg source="..."> witnesses;
- switch A/B/C/etc. readings using Verovio's appXPathQuery;
- move between Verovio pages.

Nothing from the MEI corpus is uploaded by this viewer. The selected MEI
files are read locally by the browser.

Important
---------
The viewer intentionally ignores raw witness filenames such as:

    V2-0779-a.mei
    V2-0779-b.mei

It expects the enriched/ folder to contain merged outputs such as:

    V2-0779.mei

Running on macOS
----------------
1. Unzip this folder somewhere convenient.

2. Open Terminal and go to the viewer folder, for example:

    cd ~/Downloads/verovio_corpus_viewer

3. Start a simple local web server:

    python3 -m http.server 8000

4. Open in your browser:

    http://localhost:8000

5. Click "Choose enriched folder" and select:

    /Users/davisengelis/SpokenSongs_CodeBook/data/mei/enriched

The browser may ask permission to read the selected folder.

Internet connection
-------------------
The viewer currently loads the Verovio WebAssembly toolkit from verovio.org,
so an internet connection is needed when the viewer starts.

Large corpus note
-----------------
When the corpus grows to ~1,500 files, the first folder indexing step may take
a little while because the browser reads the MEI headers locally. Progress is
shown while this happens. After indexing, searching and switching songs are
immediate.

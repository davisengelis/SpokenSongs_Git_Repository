# Spoken Songs MEI Variant Viewer

This small browser-based viewer loads a merged MEI file and automatically
discovers all `<rdg source="...">` witness references.

It then creates one button per witness and uses Verovio's `appXPathQuery`
option to render the selected reading.

## Files

- `index.html`
- `viewer.js`

## Run on macOS

Because browsers can restrict local JavaScript/file behavior, the safest way
to run it is from a tiny local web server.

Open Terminal and run:

    cd /path/to/verovio_generic_viewer
    python3 -m http.server 8000

Then open:

    http://localhost:8000

Choose one of your merged enriched MEI files, for example:

    V2-0779.mei

The viewer reads the file locally in your browser; it does not upload the MEI
to a server.

An internet connection is currently required because `index.html` loads the
Verovio WebAssembly toolkit from verovio.org.

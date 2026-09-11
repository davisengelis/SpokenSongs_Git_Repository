let toolkit = null;
let corpus = [];
let filteredCorpus = [];
let selectedSong = null;
let currentMEI = "";
let currentWitness = null;
let currentPage = 1;
let selectedMeasure = null;

const analysis = {
  songs: new Map(),
  pairs: new Map(),
  measures: new Map()
};

const folderInput = document.getElementById("folder-input");
const analysisInput = document.getElementById("analysis-input");
const searchInput = document.getElementById("search");
const variantsOnly = document.getElementById("variants-only");
const analysedOnly = document.getElementById("analysed-only");
const corpusStats = document.getElementById("corpus-stats");
const analysisStats = document.getElementById("analysis-stats");
const indexProgress = document.getElementById("index-progress");
const songList = document.getElementById("song-list");
const songHeading = document.getElementById("song-heading");
const songMeta = document.getElementById("song-meta");
const readingLabel = document.getElementById("reading-label");
const witnessButtons = document.getElementById("witness-buttons");
const statusEl = document.getElementById("status");
const notation = document.getElementById("notation");
const prevButton = document.getElementById("prev-page");
const nextButton = document.getElementById("next-page");
const pageStatus = document.getElementById("page-status");
const analysisSubtitle = document.getElementById("analysis-subtitle");
const summaryCards = document.getElementById("summary-cards");
const pairwiseTable = document.getElementById("pairwise-table");
const measureTable = document.getElementById("measure-table");

function parseXml(xmlText) {
  const doc = new DOMParser().parseFromString(xmlText, "application/xml");
  if (doc.querySelector("parsererror")) throw new Error("Invalid XML");
  return doc;
}

function getElements(docOrEl, localName) {
  return Array.from(docOrEl.getElementsByTagNameNS("*", localName));
}

function discoverWitnesses(doc) {
  const refs = new Set();
  for (const rdg of getElements(doc, "rdg")) {
    const source = rdg.getAttribute("source");
    if (!source) continue;
    for (const token of source.trim().split(/\s+/)) if (token) refs.add(token);
  }
  return Array.from(refs).sort();
}

function discoverIdentifier(doc) {
  const ids = getElements(doc, "identifier");
  const v = ids.find(el => el.getAttribute("type") === "VitolinsSource");
  return v ? v.textContent.trim() : (ids[0]?.textContent.trim() || "");
}

function discoverTitle(doc) {
  const work = getElements(doc, "work")[0];
  const title = work ? getElements(work, "title")[0] : getElements(doc, "title")[0];
  return title?.textContent.trim() || "";
}

function sourceNumberFromFilename(name) {
  const m = name.match(/^V2-(\d+)/i);
  return m ? Number(m[1]) : Number.MAX_SAFE_INTEGER;
}

function witnessLabel(ref) {
  const m = ref.match(/_([a-z])$/i);
  return m ? m[1].toUpperCase() : ref.replace(/^#/, "");
}

function boolValue(value) {
  return String(value).trim().toLowerCase() === "true";
}

function parseCSV(text) {
  // Handles ordinary RFC4180-style quoted CSV, including commas in quoted fields.
  const rows = [];
  let row = [], field = "", quoted = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];

    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"'; i++;
        } else {
          quoted = false;
        }
      } else {
        field += ch;
      }
    } else {
      if (ch === '"') quoted = true;
      else if (ch === ',') { row.push(field); field = ""; }
      else if (ch === '\n') {
        row.push(field);
        rows.push(row);
        row = []; field = "";
      } else if (ch !== '\r') field += ch;
    }
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }

  if (!rows.length) return [];
  const headers = rows[0].map((h, i) => i === 0 ? h.replace(/^\uFEFF/, "") : h);

  return rows.slice(1)
    .filter(r => r.some(v => v !== ""))
    .map(r => Object.fromEntries(headers.map((h, i) => [h, r[i] ?? ""])));
}

async function loadAnalysisFiles(fileList) {
  analysis.songs.clear();
  analysis.pairs.clear();
  analysis.measures.clear();

  const files = Array.from(fileList).filter(f => /\.csv$/i.test(f.name));

  for (const file of files) {
    const rows = parseCSV(await file.text());

    if (file.name.includes("song_summary")) {
      for (const row of rows) analysis.songs.set(Number(row.source_number), row);
    } else if (file.name.includes("pairwise_summary")) {
      for (const row of rows) {
        const source = Number(row.source_number);
        if (!analysis.pairs.has(source)) analysis.pairs.set(source, []);
        analysis.pairs.get(source).push(row);
      }
    } else if (file.name.includes("measure_comparisons")) {
      for (const row of rows) {
        const source = Number(row.source_number);
        if (!analysis.measures.has(source)) analysis.measures.set(source, []);
        analysis.measures.get(source).push(row);
      }
    }
  }

  const count = analysis.songs.size;
  analysisStats.textContent = count
    ? `${count} songs with analysis loaded`
    : "Analysis CSVs not recognized.";

  analysedOnly.disabled = !count;
  applyFilters();
  renderAnalysis();
}

async function indexFiles(fileList) {
  const files = Array.from(fileList)
    .filter(file => /\.mei$/i.test(file.name))
    .filter(file => !/-[a-z]\.mei$/i.test(file.name));

  corpus = [];
  selectedSong = null;
  notation.innerHTML = "";
  songHeading.textContent = "Indexing corpus…";
  searchInput.disabled = true;
  variantsOnly.disabled = true;

  if (!files.length) {
    corpusStats.textContent = "No merged .mei files found.";
    return;
  }

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    indexProgress.textContent = `Indexing ${i + 1} of ${files.length}: ${file.name}`;

    try {
      const xmlText = await file.text();
      const doc = parseXml(xmlText);
      const witnesses = discoverWitnesses(doc);
      corpus.push({
        file,
        filename: file.name,
        sourceNumber: sourceNumberFromFilename(file.name),
        identifier: discoverIdentifier(doc),
        title: discoverTitle(doc),
        witnesses,
        hasVariants: witnesses.length > 1
      });
    } catch (error) {
      console.warn(`Skipped ${file.name}:`, error);
    }

    if (i % 25 === 0) await new Promise(resolve => setTimeout(resolve, 0));
  }

  corpus.sort((a,b) => a.sourceNumber - b.sourceNumber || a.filename.localeCompare(b.filename));
  const variants = corpus.filter(s => s.hasVariants).length;
  corpusStats.textContent = `${corpus.length} merged MEI files — ${variants} with variants`;
  indexProgress.textContent = "";
  searchInput.disabled = false;
  variantsOnly.disabled = false;
  applyFilters();

  songHeading.textContent = corpus.length
    ? "Choose a song from the corpus index."
    : "No readable merged MEI files found.";
}

function applyFilters() {
  const query = searchInput.value.trim().toLowerCase();
  const onlyVariants = variantsOnly.checked;
  const onlyAnalysed = analysedOnly.checked;

  filteredCorpus = corpus.filter(song => {
    if (onlyVariants && !song.hasVariants) return false;
    if (onlyAnalysed && !analysis.songs.has(song.sourceNumber)) return false;
    if (!query) return true;
    return [song.identifier, song.title, song.filename, String(song.sourceNumber)]
      .join(" ").toLowerCase().includes(query);
  });

  renderSongList();
}

function renderSongList() {
  songList.innerHTML = "";

  if (!filteredCorpus.length) {
    songList.innerHTML = '<div class="empty">No matching songs.</div>';
    return;
  }

  for (const song of filteredCorpus) {
    const button = document.createElement("button");
    button.className = "song-item";
    if (selectedSong?.file === song.file) button.classList.add("active");

    const source = document.createElement("div");
    source.className = "song-source";
    source.textContent = song.identifier || song.filename;

    const title = document.createElement("div");
    title.className = "song-title";
    title.textContent = song.title || "(untitled)";

    const extra = document.createElement("div");
    extra.className = "song-extra";
    extra.textContent = song.witnesses.length > 1
      ? `Variants: ${song.witnesses.map(witnessLabel).join(", ")}`
      : "Single reading";

    button.append(source, title, extra);

    const row = analysis.songs.get(song.sourceNumber);
    if (row) {
      const badges = document.createElement("div");
      badges.className = "badges";

      const b1 = document.createElement("span");
      b1.className = "badge analysis";
      b1.textContent = `${row.variant_measure_pct}% variant`;

      const b2 = document.createElement("span");
      b2.className = "badge";
      b2.textContent = `pitch ${row.pitch_variant_measures}`;

      const b3 = document.createElement("span");
      b3.className = "badge";
      b3.textContent = `rhythm ${row.rhythm_variant_measures}`;

      const b4 = document.createElement("span");
      b4.className = "badge";
      b4.textContent = `text ${row.text_variant_measures}`;

      badges.append(b1, b2, b3, b4);
      button.appendChild(badges);
    }

    button.addEventListener("click", () => openSong(song));
    songList.appendChild(button);
  }
}

async function openSong(song) {
  if (!toolkit) {
    statusEl.textContent = "Verovio is still loading.";
    return;
  }

  selectedSong = song;
  currentMEI = await song.file.text();
  currentPage = 1;
  currentWitness = song.witnesses.length ? song.witnesses[0] : null;
  selectedMeasure = null;

  songHeading.textContent = song.title || song.filename;
  songMeta.textContent = [song.identifier, song.filename].filter(Boolean).join(" — ");

  renderSongList();
  renderWitnessButtons();
  loadSelectedReading();
  renderAnalysis();
}

function renderWitnessButtons() {
  witnessButtons.innerHTML = "";
  if (!selectedSong) { readingLabel.textContent = ""; return; }

  readingLabel.textContent = "Reading:";

  if (selectedSong.witnesses.length <= 1) {
    witnessButtons.textContent = "single";
    return;
  }

  for (const ref of selectedSong.witnesses) {
    const button = document.createElement("button");
    button.textContent = witnessLabel(ref);
    if (ref === currentWitness) button.classList.add("active");
    button.addEventListener("click", () => {
      currentWitness = ref;
      currentPage = 1;
      renderWitnessButtons();
      loadSelectedReading();
    });
    witnessButtons.appendChild(button);
  }
}

function loadSelectedReading() {
  if (!toolkit || !currentMEI || !selectedSong) return;

  const options = {
    pageWidth: Math.max(1200, document.documentElement.clientWidth * 10),
    pageHeight: 1700,
    scale: 45,
    scaleToPageSize: true,
    adjustPageHeight: true
  };

  if (currentWitness) {
    options.appXPathQuery = [`./rdg[@source='${currentWitness}']`];
  }

  toolkit.setOptions(options);
  toolkit.loadData(currentMEI);
  renderPage();
}

function renderPage() {
  if (!toolkit) return;
  notation.innerHTML = toolkit.renderToSVG(currentPage);
  const count = toolkit.getPageCount();
  pageStatus.textContent = count ? `${currentPage} / ${count}` : "";
  prevButton.disabled = currentPage <= 1;
  nextButton.disabled = currentPage >= count;

  if (selectedSong) {
    const reading = currentWitness ? witnessLabel(currentWitness) : "single";
    statusEl.textContent = `${selectedSong.identifier || selectedSong.filename} — reading ${reading}`;
  }

  if (selectedMeasure !== null) highlightMeasure(selectedMeasure, false);
}

function currentWitnessLetter() {
  if (!currentWitness) return null;
  const m = currentWitness.match(/_([a-z])$/i);
  return m ? m[1].toLowerCase() : null;
}

function measureXmlIdForReading(measureNumber) {
  if (!currentMEI || !currentWitness) return null;
  const doc = parseXml(currentMEI);

  for (const app of getElements(doc, "app")) {
    for (const rdg of getElements(app, "rdg")) {
      if (rdg.getAttribute("source") !== currentWitness) continue;
      const measure = getElements(rdg, "measure")
        .find(m => String(m.getAttribute("n")) === String(measureNumber));
      if (measure) {
        return measure.getAttributeNS("http://www.w3.org/XML/1998/namespace", "id")
          || measure.getAttribute("xml:id")
          || null;
      }
    }
  }
  return null;
}

function highlightMeasure(measureNumber, scroll = true) {
  selectedMeasure = Number(measureNumber);

  document.querySelectorAll(".analysis-highlight").forEach(el =>
    el.classList.remove("analysis-highlight")
  );

  const xmlId = measureXmlIdForReading(selectedMeasure);
  if (!xmlId) return;

  const target = document.getElementById(xmlId);
  if (target) {
    target.classList.add("analysis-highlight");
    if (scroll) target.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
  }

  document.querySelectorAll("tr.measure-row").forEach(row => {
    row.classList.toggle("selected", Number(row.dataset.measure) === selectedMeasure);
  });
}

function card(value, label) {
  const el = document.createElement("div");
  el.className = "summary-card";
  el.innerHTML = `<div class="summary-value">${value}</div><div class="summary-label">${label}</div>`;
  return el;
}

function renderAnalysis() {
  summaryCards.innerHTML = "";
  pairwiseTable.innerHTML = "";
  measureTable.innerHTML = "";

  if (!selectedSong) {
    analysisSubtitle.textContent = analysis.songs.size
      ? "Choose an analysed song."
      : "Load the analysis CSVs to show analysis here.";
    pairwiseTable.textContent = "No song selected.";
    measureTable.textContent = "No song selected.";
    return;
  }

  const source = selectedSong.sourceNumber;
  const song = analysis.songs.get(source);
  const pairs = analysis.pairs.get(source) || [];
  const measures = analysis.measures.get(source) || [];

  if (!song) {
    analysisSubtitle.textContent = "No analysis record for this song.";
    pairwiseTable.textContent = "Not analysed.";
    measureTable.textContent = "Not analysed.";
    return;
  }

  analysisSubtitle.textContent =
    `${selectedSong.identifier || selectedSong.filename} — ${song.pairwise_comparisons} pairwise witness comparison(s)`;

  summaryCards.append(
    card(`${song.variant_measure_pct}%`, "Pairwise measures with any variation"),
    card(song.pitch_variant_measures, "Pitch-variant measures"),
    card(song.rhythm_variant_measures, "Rhythm-variant measures"),
    card(song.text_variant_measures, "Text-variant measures"),
    card(`${song.contour_preserved_pct_of_variant}%`, "Variant measures with preserved contour"),
    card(`${song.small_pitch_change_pct}%`, "Pitch substitutions ≤ 2 semitones")
  );

  renderPairwiseTable(pairs);
  renderMeasureTable(measures);
}

function renderPairwiseTable(rows) {
  if (!rows.length) {
    pairwiseTable.textContent = "No pairwise rows.";
    return;
  }

  const table = document.createElement("table");
  table.innerHTML = `
    <thead><tr>
      <th>Pair</th><th>Measures</th><th>Variant</th>
      <th>Pitch</th><th>Rhythm</th><th>Text</th>
      <th>Contour preserved</th><th>Small pitch changes</th>
    </tr></thead>`;

  const body = document.createElement("tbody");

  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.witness_a.toUpperCase()} ↔ ${row.witness_b.toUpperCase()}</td>
      <td>${row.measures_compared}</td>
      <td>${row.variant_measures} (${row.variant_measure_pct}%)</td>
      <td>${row.pitch_variant_measures}</td>
      <td>${row.rhythm_variant_measures}</td>
      <td>${row.text_variant_measures}</td>
      <td>${row.contour_preserved_pct_of_variant}%</td>
      <td>${row.small_pitch_change_pct}%</td>`;
    body.appendChild(tr);
  }

  table.appendChild(body);
  pairwiseTable.appendChild(table);
}

function renderMeasureTable(rows) {
  if (!rows.length) {
    measureTable.textContent = "No measure rows.";
    return;
  }

  const currentLetter = currentWitnessLetter();

  // For a three-witness song, keep all pairwise rows; current-reading relevance
  // is easy to see from the pair labels.
  const sorted = [...rows].sort((a,b) =>
    Number(a.measure) - Number(b.measure) ||
    a.witness_a.localeCompare(b.witness_a) ||
    a.witness_b.localeCompare(b.witness_b)
  );

  const table = document.createElement("table");
  table.innerHTML = `
    <thead><tr>
      <th>Measure</th><th>Pair</th><th>Variation</th>
      <th>Contour</th><th>Pitch substitutions</th>
      <th>≤2 st</th><th>&gt;2 st</th><th>Max deviation</th>
    </tr></thead>`;

  const body = document.createElement("tbody");

  for (const row of sorted) {
    const tr = document.createElement("tr");
    tr.className = "measure-row";
    tr.dataset.measure = row.measure;

    const kinds = row.variation_type === "identical"
      ? '<span class="kind">identical</span>'
      : row.variation_type.split("+").map(k => `<span class="kind">${k}</span>`).join("");

    const pairRelevant = currentLetter &&
      (row.witness_a.toLowerCase() === currentLetter || row.witness_b.toLowerCase() === currentLetter);

    if (pairRelevant) tr.dataset.currentReading = "true";

    tr.innerHTML = `
      <td>${row.measure}</td>
      <td>${row.witness_a.toUpperCase()} ↔ ${row.witness_b.toUpperCase()}</td>
      <td>${kinds}</td>
      <td>${boolValue(row.contour_preserved) ? "yes" : "no"}</td>
      <td>${row.pitch_substitutions}</td>
      <td>${row.small_pitch_changes_le_2_semitones}</td>
      <td>${row.larger_pitch_changes_gt_2_semitones}</td>
      <td>${row.max_pitch_deviation_semitones} st</td>`;

    tr.addEventListener("click", () => highlightMeasure(row.measure));
    body.appendChild(tr);
  }

  table.appendChild(body);
  measureTable.appendChild(table);
}

prevButton.addEventListener("click", () => {
  currentPage = Math.max(1, currentPage - 1);
  renderPage();
});
nextButton.addEventListener("click", () => {
  currentPage = Math.min(toolkit.getPageCount(), currentPage + 1);
  renderPage();
});

searchInput.addEventListener("input", applyFilters);
variantsOnly.addEventListener("change", applyFilters);
analysedOnly.addEventListener("change", applyFilters);
folderInput.addEventListener("change", e => indexFiles(e.target.files));
analysisInput.addEventListener("change", e => loadAnalysisFiles(e.target.files));

window.addEventListener("resize", () => {
  if (toolkit && selectedSong && currentMEI) loadSelectedReading();
});

document.addEventListener("DOMContentLoaded", () => {
  verovio.module.onRuntimeInitialized = function () {
    toolkit = new verovio.toolkit();
    statusEl.textContent = "Verovio ready. Choose your enriched folder.";
  };
});

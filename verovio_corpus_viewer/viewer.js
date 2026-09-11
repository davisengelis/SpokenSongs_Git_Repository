let toolkit = null;
let corpus = [];
let filteredCorpus = [];
let selectedSong = null;
let currentMEI = "";
let currentWitness = null;
let currentPage = 1;

const folderInput = document.getElementById("folder-input");
const filesInput = document.getElementById("files-input");
const searchInput = document.getElementById("search");
const variantsOnly = document.getElementById("variants-only");
const corpusStats = document.getElementById("corpus-stats");
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

function parseXml(xmlText) {
  const doc = new DOMParser().parseFromString(xmlText, "application/xml");
  if (doc.querySelector("parsererror")) {
    throw new Error("Invalid XML");
  }
  return doc;
}

function textOfFirst(elements) {
  if (!elements || !elements.length) return "";
  return (elements[0].textContent || "").trim();
}

function getElements(doc, localName) {
  return Array.from(doc.getElementsByTagNameNS("*", localName));
}

function discoverWitnesses(doc) {
  const refs = new Set();
  for (const rdg of getElements(doc, "rdg")) {
    const source = rdg.getAttribute("source");
    if (!source) continue;
    for (const token of source.trim().split(/\s+/)) {
      if (token) refs.add(token);
    }
  }
  return Array.from(refs).sort();
}

function discoverIdentifier(doc) {
  const identifiers = getElements(doc, "identifier");
  const vitolins = identifiers.find(el => el.getAttribute("type") === "VitolinsSource");
  if (vitolins && vitolins.textContent.trim()) return vitolins.textContent.trim();
  return identifiers.length ? identifiers[0].textContent.trim() : "";
}

function discoverTitle(doc) {
  const works = getElements(doc, "work");
  if (works.length) {
    const titles = getElements(works[0], "title");
    if (titles.length && titles[0].textContent.trim()) {
      return titles[0].textContent.trim();
    }
  }
  return textOfFirst(getElements(doc, "title"));
}

function sourceNumberFromFilename(name) {
  const match = name.match(/^V2-(\d+)/i);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

function witnessLabel(ref) {
  const match = ref.match(/_([a-z])$/i);
  return match ? match[1].toUpperCase() : ref.replace(/^#/, "");
}

async function indexFiles(fileList) {
  const files = Array.from(fileList)
    .filter(file => /\.mei$/i.test(file.name))
    .filter(file => !/-[a-z]\.mei$/i.test(file.name));

  corpus = [];
  selectedSong = null;
  notation.innerHTML = "";
  songHeading.textContent = "Indexing corpus…";
  songMeta.textContent = "";
  searchInput.disabled = true;
  variantsOnly.disabled = true;

  if (!files.length) {
    corpusStats.textContent = "No merged .mei files found.";
    indexProgress.textContent =
      "The folder should contain enriched files such as V2-0001.mei.";
    songList.innerHTML = "";
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

    // Allow the browser to repaint during large corpus indexing.
    if (i % 25 === 0) {
      await new Promise(resolve => setTimeout(resolve, 0));
    }
  }

  corpus.sort((a, b) =>
    a.sourceNumber - b.sourceNumber ||
    a.filename.localeCompare(b.filename)
  );

  const variantSongs = corpus.filter(song => song.hasVariants).length;
  corpusStats.textContent =
    `${corpus.length} merged MEI files — ${variantSongs} with variants`;
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

  filteredCorpus = corpus.filter(song => {
    if (onlyVariants && !song.hasVariants) return false;

    if (!query) return true;

    const haystack = [
      song.identifier,
      song.title,
      song.filename,
      String(song.sourceNumber)
    ].join(" ").toLowerCase();

    return haystack.includes(query);
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

    if (selectedSong && selectedSong.file === song.file) {
      button.classList.add("active");
    }

    const source = document.createElement("div");
    source.className = "song-source";
    source.textContent = song.identifier || song.filename;

    const title = document.createElement("div");
    title.className = "song-title";
    title.textContent = song.title || "(untitled)";

    const extra = document.createElement("div");
    extra.className = "song-extra";

    if (song.witnesses.length > 1) {
      extra.textContent =
        `Variants: ${song.witnesses.map(witnessLabel).join(", ")}`;
    } else {
      extra.textContent = "Single reading";
    }

    button.append(source, title, extra);
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

  songHeading.textContent = song.title || song.filename;
  songMeta.textContent = [
    song.identifier,
    song.filename
  ].filter(Boolean).join(" — ");

  renderSongList();
  renderWitnessButtons();
  loadSelectedReading();
}

function renderWitnessButtons() {
  witnessButtons.innerHTML = "";

  if (!selectedSong || selectedSong.witnesses.length <= 1) {
    readingLabel.textContent = selectedSong ? "Reading:" : "";
    if (selectedSong) {
      const span = document.createElement("span");
      span.textContent = "single";
      witnessButtons.appendChild(span);
    }
    return;
  }

  readingLabel.textContent = "Reading:";

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
    options.appXPathQuery = [
      `./rdg[@source='${currentWitness}']`
    ];
  }

  toolkit.setOptions(options);
  toolkit.loadData(currentMEI);
  renderPage();
}

function renderPage() {
  if (!toolkit) return;

  notation.innerHTML = toolkit.renderToSVG(currentPage);
  const count = toolkit.getPageCount();

  pageStatus.textContent = count
    ? `${currentPage} / ${count}`
    : "";

  prevButton.disabled = currentPage <= 1;
  nextButton.disabled = currentPage >= count;

  if (selectedSong) {
    const reading = currentWitness
      ? witnessLabel(currentWitness)
      : "single";

    statusEl.textContent =
      `${selectedSong.identifier || selectedSong.filename} — reading ${reading}`;
  }
}

prevButton.addEventListener("click", () => {
  if (!toolkit) return;
  currentPage = Math.max(1, currentPage - 1);
  renderPage();
});

nextButton.addEventListener("click", () => {
  if (!toolkit) return;
  currentPage = Math.min(toolkit.getPageCount(), currentPage + 1);
  renderPage();
});

searchInput.addEventListener("input", applyFilters);
variantsOnly.addEventListener("change", applyFilters);

folderInput.addEventListener("change", event => {
  indexFiles(event.target.files);
});

filesInput.addEventListener("change", event => {
  indexFiles(event.target.files);
});

window.addEventListener("resize", () => {
  if (toolkit && selectedSong && currentMEI) {
    loadSelectedReading();
  }
});

document.addEventListener("DOMContentLoaded", () => {
  verovio.module.onRuntimeInitialized = function () {
    toolkit = new verovio.toolkit();
    statusEl.textContent =
      "Verovio ready. Choose your enriched folder.";
  };
});

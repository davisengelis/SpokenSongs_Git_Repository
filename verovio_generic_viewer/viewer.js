let toolkit = null;
let currentMEI = "";
let currentWitness = null;
let currentPage = 1;
let witnessRefs = [];

const fileInput = document.getElementById("file-input");
const witnessButtons = document.getElementById("witness-buttons");
const notation = document.getElementById("notation");
const statusEl = document.getElementById("status");
const metaEl = document.getElementById("meta");
const prevButton = document.getElementById("prev-page");
const nextButton = document.getElementById("next-page");

function witnessLabelFromRef(ref) {
  const match = ref.match(/_([a-z])$/i);
  return match ? match[1].toUpperCase() : ref.replace(/^#/, "");
}

function discoverWitnesses(xmlText) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(xmlText, "application/xml");

  if (doc.querySelector("parsererror")) {
    throw new Error("The selected file is not valid XML.");
  }

  const refs = new Set();

  for (const rdg of doc.getElementsByTagNameNS("*", "rdg")) {
    const source = rdg.getAttribute("source");
    if (source) {
      for (const token of source.trim().split(/\s+/)) {
        if (token) refs.add(token);
      }
    }
  }

  return Array.from(refs).sort();
}

function discoverTitle(xmlText) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(xmlText, "application/xml");

  const work = doc.getElementsByTagNameNS("*", "work")[0];
  if (work) {
    const title = work.getElementsByTagNameNS("*", "title")[0];
    if (title && title.textContent.trim()) return title.textContent.trim();
  }

  const title = doc.getElementsByTagNameNS("*", "title")[0];
  return title ? title.textContent.trim() : "";
}

function discoverIdentifier(xmlText) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(xmlText, "application/xml");
  const ids = Array.from(doc.getElementsByTagNameNS("*", "identifier"));

  const vitolins = ids.find(el => el.getAttribute("type") === "VitolinsSource");
  if (vitolins && vitolins.textContent.trim()) return vitolins.textContent.trim();

  return ids.length ? ids[0].textContent.trim() : "";
}

function setWitnessButtons() {
  witnessButtons.innerHTML = "";

  if (witnessRefs.length === 0) {
    witnessButtons.textContent = "No <rdg> witnesses found";
    return;
  }

  for (const ref of witnessRefs) {
    const button = document.createElement("button");
    button.textContent = witnessLabelFromRef(ref);
    button.dataset.ref = ref;

    if (ref === currentWitness) button.classList.add("active");

    button.addEventListener("click", () => {
      currentWitness = ref;
      currentPage = 1;
      setWitnessButtons();
      loadSelectedReading();
    });

    witnessButtons.appendChild(button);
    witnessButtons.appendChild(document.createTextNode(" "));
  }
}

function updatePageButtons() {
  const count = toolkit ? toolkit.getPageCount() : 0;
  prevButton.disabled = !toolkit || currentPage <= 1;
  nextButton.disabled = !toolkit || currentPage >= count;
}

function renderPage() {
  if (!toolkit) return;

  notation.innerHTML = toolkit.renderToSVG(currentPage);

  const pageCount = toolkit.getPageCount();
  const witnessName = currentWitness
    ? witnessLabelFromRef(currentWitness)
    : "default";

  statusEl.textContent =
    `Reading ${witnessName} — page ${currentPage} of ${pageCount}`;

  updatePageButtons();
}

function loadSelectedReading() {
  if (!toolkit || !currentMEI) return;

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

fileInput.addEventListener("change", async event => {
  const file = event.target.files[0];
  if (!file) return;

  try {
    currentMEI = await file.text();
    witnessRefs = discoverWitnesses(currentMEI);

    if (witnessRefs.length === 0) {
      currentWitness = null;
    } else {
      currentWitness = witnessRefs[0];
    }

    currentPage = 1;
    setWitnessButtons();

    const title = discoverTitle(currentMEI);
    const identifier = discoverIdentifier(currentMEI);

    metaEl.textContent = [identifier, title].filter(Boolean).join(" — ");

    loadSelectedReading();
  } catch (error) {
    notation.innerHTML = "";
    statusEl.textContent = `Error: ${error.message}`;
    metaEl.textContent = "";
  }
});

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

window.addEventListener("resize", () => {
  if (toolkit && currentMEI) {
    loadSelectedReading();
  }
});

document.addEventListener("DOMContentLoaded", () => {
  verovio.module.onRuntimeInitialized = function () {
    toolkit = new verovio.toolkit();
    statusEl.textContent = "Verovio ready. Choose a merged MEI file.";
  };
});

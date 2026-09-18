// Application state
let selectedCompressFile = null;
let selectedCompressLevel = "medium";
let compressPollInterval = null;
let lastCompressPct = 0;
let compressStartTime = null;

let selectedWmFile = null;
let wmJobId = null;
let wmCandidates = [];
let selectedCandidateId = null;
let wmPollInterval = null;
let lastWmPct = 0;

const LEVEL_DESCRIPTIONS = {
  low: "Reduces size by 10–30%, high quality (prepress)",
  medium: "Reduces size by 30–60%, balanced quality",
  high: "Reduces size by 50–80%, smallest size"
};

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", () => {
  setupDropzone("compress-dropzone", "compress-file-input", handleCompressFileSelect);
  setupDropzone("wm-dropzone", "wm-file-input", handleWmFileSelect);
  checkSystemStatus();
});

function switchTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
  document.querySelectorAll(".tab-pane").forEach(content => content.classList.remove("active"));

  if (tabName === "compress") {
    document.getElementById("tab-btn-compress").classList.add("active");
    document.getElementById("tab-compress").classList.add("active");
  } else {
    document.getElementById("tab-btn-watermark").classList.add("active");
    document.getElementById("tab-watermark").classList.add("active");
  }
}

async function checkSystemStatus() {
  try {
    const res = await fetch("/api/system/status");
    if (res.ok) {
      const data = await res.json();
      const alertBox = document.getElementById("gs-alert");
      if (!data.ghostscript_available) {
        alertBox.style.display = "block";
      } else {
        alertBox.style.display = "none";
      }
    }
  } catch (e) {
    // API offline
  }
}

// ==========================================
// Drag and Drop Helper
// ==========================================
function setupDropzone(dropzoneId, inputId, onFileSelect) {
  const dropzone = document.getElementById(dropzoneId);
  const input = document.getElementById(inputId);

  dropzone.addEventListener("click", () => input.click());

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });

  ["dragleave", "dragend"].forEach(type => {
    dropzone.addEventListener(type, () => dropzone.classList.remove("dragover"));
  });

  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      onFileSelect(e.dataTransfer.files[0]);
    }
  });

  input.addEventListener("change", (e) => {
    if (e.target.files && e.target.files.length > 0) {
      onFileSelect(e.target.files[0]);
    }
  });
}

// ==========================================
// Compression
// ==========================================
function handleCompressFileSelect(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showError("compress", "Please select a valid PDF document.");
    return;
  }
  selectedCompressFile = file;
  document.getElementById("compress-file-name").textContent = file.name;
  document.getElementById("compress-file-size").textContent = (file.size / (1024 * 1024)).toFixed(2) + " MB";
  document.getElementById("compress-file-badge").style.display = "flex";
  document.getElementById("btn-compress-submit").disabled = false;
  hideError("compress");
  document.getElementById("compress-result").style.display = "none";
}

function selectLevel(level) {
  selectedCompressLevel = level;
  const radio = document.querySelector(`input[name="compress-level"][value="${level}"]`);
  if (radio) radio.checked = true;

  const desc = document.getElementById("level-detail-text");
  if (desc) {
    desc.textContent = LEVEL_DESCRIPTIONS[level] || "";
  }
}

async function startCompression() {
  if (!selectedCompressFile) return;

  hideError("compress");
  document.getElementById("compress-result").style.display = "none";
  document.getElementById("btn-compress-submit").disabled = true;

  const progressBox = document.getElementById("compress-progress");
  const spinnerMode = document.getElementById("compress-spinner-mode");
  const barMode = document.getElementById("compress-bar-mode");

  progressBox.style.display = "block";
  spinnerMode.style.display = "flex";
  barMode.style.display = "none";
  document.getElementById("compress-spinner-text").textContent = "Compressing PDF...";

  lastCompressPct = 0;
  compressStartTime = Date.now();

  const formData = new FormData();
  formData.append("file", selectedCompressFile);
  formData.append("level", selectedCompressLevel);

  try {
    const res = await fetch("/api/compress", {
      method: "POST",
      body: formData
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to start compression.");
    }

    const data = await res.json();
    pollCompressionStatus(data.job_id);
  } catch (err) {
    showError("compress", err.message);
    progressBox.style.display = "none";
    document.getElementById("btn-compress-submit").disabled = false;
  }
}

function pollCompressionStatus(jobId) {
  if (compressPollInterval) clearInterval(compressPollInterval);

  const progressBox = document.getElementById("compress-progress");
  const spinnerMode = document.getElementById("compress-spinner-mode");
  const barMode = document.getElementById("compress-bar-mode");
  const progressBar = document.getElementById("compress-progress-bar");
  const progressStatus = document.getElementById("compress-progress-status");
  const progressDetail = document.getElementById("compress-progress-detail");

  compressPollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/compress/status/${jobId}`);
      if (!res.ok) throw new Error("Unable to fetch compression progress.");

      const job = await res.json();
      const elapsedMs = Date.now() - (compressStartTime || Date.now());
      const elapsedSec = Math.floor(elapsedMs / 1000);

      if (job.status === "processing") {
        if (elapsedMs >= 2500) {
          if (spinnerMode.style.display !== "none") {
            spinnerMode.style.display = "none";
            barMode.style.display = "block";
          }
          const rawPct = Math.min(95, Math.max(10, job.progress || Math.min(95, elapsedSec * 5)));
          lastCompressPct = Math.max(lastCompressPct, rawPct);
          const pct = lastCompressPct;

          progressBar.style.width = `${pct}%`;
          progressStatus.textContent = "Compressing PDF...";
          progressDetail.textContent = `(${elapsedSec}s elapsed)`;
        } else {
          document.getElementById("compress-spinner-text").textContent = `Compressing PDF... (${elapsedSec}s)`;
        }

      } else if (job.status === "done") {
        clearInterval(compressPollInterval);
        compressPollInterval = null;

        if (elapsedMs < 2500) {
          progressBox.style.display = "none";
        } else {
          progressBar.style.width = "100%";
          progressStatus.textContent = "Complete";
          progressDetail.textContent = `(${elapsedSec}s)`;
          setTimeout(() => { progressBox.style.display = "none"; }, 250);
        }

        renderCompressionResult(job);

      } else if (job.status === "error") {
        clearInterval(compressPollInterval);
        compressPollInterval = null;
        progressBox.style.display = "none";
        document.getElementById("btn-compress-submit").disabled = false;
        showError("compress", job.error_message || "An error occurred during compression.");
      }
    } catch (e) {
      clearInterval(compressPollInterval);
      compressPollInterval = null;
      document.getElementById("compress-progress").style.display = "none";
      document.getElementById("btn-compress-submit").disabled = false;
      showError("compress", e.message);
    }
  }, 350);
}

function renderCompressionResult(job) {
  const resultBox = document.getElementById("compress-result");
  const optimalNotice = document.getElementById("compress-optimal-notice");
  const btnText = document.getElementById("compress-download-btn-text");
  const resultMarker = document.getElementById("compress-result-marker");

  const origMb = Number(job.original_size_mb || 0).toFixed(2);
  const compMb = Number(job.compressed_size_mb || origMb).toFixed(2);

  // Single accurate size display (bug fixed)
  document.getElementById("chart-orig-val").textContent = `${origMb} MB`;
  document.getElementById("chart-comp-val").textContent = `${compMb} MB`;

  // Proportional 6px horizontal bars
  const origVal = Math.max(0.01, Number(job.original_size_mb || 0));
  const compVal = Math.max(0.01, Number(job.compressed_size_mb || origVal));
  const compRatio = Math.min(100, Math.max(3, Math.round((compVal / origVal) * 100)));

  document.getElementById("chart-orig-bar").style.width = "100%";
  document.getElementById("chart-comp-bar").style.width = `${compRatio}%`;

  if (job.already_optimal) {
    resultBox.classList.remove("state-success");
    resultBox.classList.add("state-optimal");
    resultMarker.textContent = "ⓘ";
    optimalNotice.style.display = "block";
    document.getElementById("metric-saved-pct").textContent = "0%";
    if (btnText) btnText.textContent = "Download original PDF";
  } else {
    resultBox.classList.remove("state-optimal");
    resultBox.classList.add("state-success");
    resultMarker.textContent = "✓";
    optimalNotice.style.display = "none";

    let savedPct = 0;
    if (job.original_size_mb > 0 && job.compressed_size_mb !== null) {
      savedPct = Math.max(0, Math.round((1 - job.compressed_size_mb / job.original_size_mb) * 100));
    }
    document.getElementById("metric-saved-pct").textContent = `${savedPct}%`;
    if (btnText) btnText.textContent = "Download compressed PDF";
  }

  const dlBtn = document.getElementById("compress-download-btn");
  dlBtn.href = job.download_url;
  resultBox.style.display = "block";
  document.getElementById("btn-compress-submit").disabled = false;
}

function resetCompress() {
  if (compressPollInterval) {
    clearInterval(compressPollInterval);
    compressPollInterval = null;
  }
  selectedCompressFile = null;
  lastCompressPct = 0;
  compressStartTime = null;

  const fileInput = document.getElementById("compress-file-input");
  if (fileInput) fileInput.value = "";

  document.getElementById("compress-file-badge").style.display = "none";
  document.getElementById("compress-progress").style.display = "none";
  document.getElementById("compress-result").style.display = "none";
  hideError("compress");
  document.getElementById("btn-compress-submit").disabled = true;
}

// ==========================================
// Watermark Removal
// ==========================================
function handleWmFileSelect(file) {
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    showError("wm", "Please select a valid PDF document.");
    return;
  }
  selectedWmFile = file;
  document.getElementById("wm-file-name").textContent = file.name;
  document.getElementById("wm-file-size").textContent = (file.size / (1024 * 1024)).toFixed(2) + " MB";
  document.getElementById("wm-file-badge").style.display = "flex";
  document.getElementById("btn-wm-detect").disabled = false;

  hideError("wm");
  document.getElementById("wm-candidates-section").style.display = "none";
  document.getElementById("wm-result").style.display = "none";
  document.getElementById("wm-scanned-warning").style.display = "none";
}

async function startWatermarkDetection() {
  if (!selectedWmFile) return;

  hideError("wm");
  document.getElementById("wm-candidates-section").style.display = "none";
  document.getElementById("wm-result").style.display = "none";
  document.getElementById("wm-scanned-warning").style.display = "none";

  const detectBtn = document.getElementById("btn-wm-detect");
  const loading = document.getElementById("wm-detect-loading");

  detectBtn.disabled = true;
  loading.style.display = "flex";

  const formData = new FormData();
  formData.append("file", selectedWmFile);

  try {
    const res = await fetch("/api/watermark/detect", {
      method: "POST",
      body: formData
    });

    loading.style.display = "none";
    detectBtn.disabled = false;

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Watermark detection failed.");
    }

    const data = await res.json();
    wmJobId = data.job_id;
    wmCandidates = data.candidates || [];

    if (data.scanned_pdf_warning) {
      document.getElementById("wm-scanned-warning").style.display = "block";
    }

    if (wmCandidates.length === 0) {
      showError("wm", "No repeated watermark pattern found across document pages.");
      return;
    }

    renderCandidates(wmCandidates);
  } catch (err) {
    loading.style.display = "none";
    detectBtn.disabled = false;
    showError("wm", err.message);
  }
}

function renderCandidates(candidates) {
  const container = document.getElementById("wm-candidates-section");
  const grid = document.getElementById("wm-candidates-grid");
  grid.innerHTML = "";

  selectedCandidateId = candidates[0].candidate_id;

  candidates.forEach((cand, idx) => {
    const card = document.createElement("div");
    card.className = `candidate-card ${idx === 0 ? "selected" : ""}`;
    card.id = `cand-${cand.candidate_id}`;
    card.onclick = () => selectCandidate(cand.candidate_id);

    const typeBadge = cand.type;
    const sample = cand.sample_text ? `"${cand.sample_text}"` : (cand.type === "image" ? "Image Logo / Stamp" : "Vector Art Stamp");
    const previewUrl = cand.page_preview_image_url;
    const coverage = cand.estimated_coverage || `Confidence: ${Math.round(cand.confidence * 100)}%`;

    card.innerHTML = `
      <img class="candidate-preview-img" src="${previewUrl}" alt="Page 1 Preview">
      <div class="candidate-details">
        <div class="candidate-type">${typeBadge}</div>
        <div class="candidate-sample">${sample}</div>
        <div class="candidate-meta">${coverage}</div>
      </div>
    `;
    grid.appendChild(card);
  });

  container.style.display = "block";
}

function selectCandidate(candidateId) {
  selectedCandidateId = candidateId;
  document.querySelectorAll(".candidate-card").forEach(c => c.classList.remove("selected"));
  const chosen = document.getElementById(`cand-${candidateId}`);
  if (chosen) chosen.classList.add("selected");
}

async function startWatermarkRemoval() {
  if (!wmJobId || !selectedCandidateId) return;

  hideError("wm");
  document.getElementById("btn-wm-remove").disabled = true;

  const progressBox = document.getElementById("wm-progress");
  const progressBar = document.getElementById("wm-progress-bar");
  const progressStatus = document.getElementById("wm-progress-status");
  const progressDetail = document.getElementById("wm-progress-detail");

  lastWmPct = 0;
  progressBox.style.display = "block";
  progressBar.style.width = "0%";
  progressStatus.textContent = "Cleaning pages...";
  progressDetail.textContent = "";

  try {
    const res = await fetch("/api/watermark/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        job_id: wmJobId,
        candidate_id: selectedCandidateId
      })
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Failed to initiate watermark removal.");
    }

    const data = await res.json();
    pollWatermarkStatus(data.job_id);
  } catch (err) {
    progressBox.style.display = "none";
    document.getElementById("btn-wm-remove").disabled = false;
    showError("wm", err.message);
  }
}

function pollWatermarkStatus(jobId) {
  if (wmPollInterval) clearInterval(wmPollInterval);

  const progressBox = document.getElementById("wm-progress");
  const progressBar = document.getElementById("wm-progress-bar");
  const progressStatus = document.getElementById("wm-progress-status");
  const progressDetail = document.getElementById("wm-progress-detail");

  wmPollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/watermark/status/${jobId}`);
      if (!res.ok) throw new Error("Unable to fetch removal progress.");

      const job = await res.json();

      if (job.status === "processing") {
        const pct = Math.max(lastWmPct, job.progress || 0);
        lastWmPct = pct;

        progressBar.style.width = `${pct}%`;

        const curPage = job.pages_cleaned || 0;
        const totalPages = job.total_pages || 0;
        const pps = job.pages_per_sec || 0;
        const remainingPages = Math.max(0, totalPages - curPage);
        const etaSec = pps > 0 && remainingPages > 0 ? Math.ceil(remainingPages / pps) : null;
        const etaText = etaSec !== null ? ` • ~${etaSec}s left` : "";
        const ppsText = pps > 0 ? ` • ${pps} p/s` : "";

        progressStatus.textContent = `Page ${curPage} of ${totalPages} (${pct}%)`;
        progressDetail.textContent = `${ppsText}${etaText}`.trim();

      } else if (job.status === "done") {
        clearInterval(wmPollInterval);
        wmPollInterval = null;

        progressBar.style.width = "100%";
        progressStatus.textContent = "Complete";
        progressDetail.textContent = `Cleaned ${job.pages_cleaned} pages`;

        setTimeout(() => { progressBox.style.display = "none"; }, 300);

        document.getElementById("wm-cleaned-count").textContent = job.pages_cleaned || 0;
        const dlBtn = document.getElementById("wm-download-btn");
        dlBtn.href = job.download_url;
        document.getElementById("wm-result").style.display = "block";
        document.getElementById("btn-wm-remove").disabled = false;

      } else if (job.status === "error") {
        clearInterval(wmPollInterval);
        wmPollInterval = null;
        progressBox.style.display = "none";
        document.getElementById("btn-wm-remove").disabled = false;
        showError("wm", job.error_message || "Watermark removal failed.");
      }
    } catch (e) {
      clearInterval(wmPollInterval);
      wmPollInterval = null;
      document.getElementById("wm-progress").style.display = "none";
      document.getElementById("btn-wm-remove").disabled = false;
      showError("wm", e.message);
    }
  }, 350);
}

function resetWatermark() {
  if (wmPollInterval) {
    clearInterval(wmPollInterval);
    wmPollInterval = null;
  }
  selectedWmFile = null;
  wmJobId = null;
  wmCandidates = [];
  selectedCandidateId = null;
  lastWmPct = 0;

  const fileInput = document.getElementById("wm-file-input");
  if (fileInput) fileInput.value = "";

  document.getElementById("wm-file-badge").style.display = "none";
  document.getElementById("wm-candidates-section").style.display = "none";
  document.getElementById("wm-progress").style.display = "none";
  document.getElementById("wm-result").style.display = "none";
  document.getElementById("wm-scanned-warning").style.display = "none";
  hideError("wm");
  document.getElementById("btn-wm-detect").disabled = true;
}

// ==========================================
// Error Display Helpers
// ==========================================
function showError(tab, message) {
  const el = document.getElementById(`${tab}-error`);
  if (el) {
    el.textContent = message;
    el.style.display = "block";
  }
}

function hideError(tab) {
  const el = document.getElementById(`${tab}-error`);
  if (el) {
    el.style.display = "none";
    el.textContent = "";
  }
}

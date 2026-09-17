// Application state
let selectedCompressFile = null;
let selectedCompressLevel = "medium";
let compressPollInterval = null;

let selectedWmFile = null;
let wmJobId = null;
let wmCandidates = [];
let selectedCandidateId = null;
let wmPollInterval = null;

// Initialize on DOM load
document.addEventListener("DOMContentLoaded", () => {
  setupDropzone("compress-dropzone", "compress-file-input", handleCompressFileSelect);
  setupDropzone("wm-dropzone", "wm-file-input", handleWmFileSelect);
  checkSystemStatus();
});

function switchTab(tabName) {
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(content => content.classList.remove("active"));

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
      if (!data.ghostscript_available) {
        document.getElementById("gs-alert").style.display = "flex";
      }
    }
  } catch (e) {
    // API not started yet or offline
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
// Feature 1: Compression Logic
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
  document.querySelectorAll(".level-btn").forEach(btn => btn.classList.remove("selected"));
  document.getElementById(`btn-level-${level}`).classList.add("selected");
}

async function startCompression() {
  if (!selectedCompressFile) return;

  hideError("compress");
  document.getElementById("compress-result").style.display = "none";
  document.getElementById("btn-compress-submit").disabled = true;

  const progressBox = document.getElementById("compress-progress");
  const progressBar = document.getElementById("compress-progress-bar");
  const progressPercent = document.getElementById("compress-progress-percent");
  const progressStatus = document.getElementById("compress-progress-status");

  progressBox.style.display = "block";
  progressBar.style.width = "10%";
  progressPercent.textContent = "10%";
  progressStatus.textContent = "Uploading PDF to local engine...";

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
    const jobId = data.job_id;

    // Start polling
    pollCompressionStatus(jobId);
  } catch (err) {
    showError("compress", err.message);
    progressBox.style.display = "none";
    document.getElementById("btn-compress-submit").disabled = false;
  }
}

function pollCompressionStatus(jobId) {
  if (compressPollInterval) clearInterval(compressPollInterval);

  compressPollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/compress/status/${jobId}`);
      if (!res.ok) {
        throw new Error("Unable to fetch compression progress.");
      }

      const job = await res.json();
      const progressBox = document.getElementById("compress-progress");
      const progressBar = document.getElementById("compress-progress-bar");
      const progressPercent = document.getElementById("compress-progress-percent");
      const progressStatus = document.getElementById("compress-progress-status");

      if (job.status === "processing") {
        const pct = Math.max(15, job.progress || 35);
        progressBar.style.width = `${pct}%`;
        progressPercent.textContent = `${pct}%`;
        progressStatus.textContent = "Compressing PDF with Ghostscript...";
      } else if (job.status === "done") {
        clearInterval(compressPollInterval);
        progressBar.style.width = "100%";
        progressPercent.textContent = "100%";
        progressStatus.textContent = "Complete!";
        setTimeout(() => { progressBox.style.display = "none"; }, 500);

        // Show result
        document.getElementById("metric-orig-size").textContent = `${job.original_size_mb} MB`;
        document.getElementById("metric-comp-size").textContent = `${job.compressed_size_mb} MB`;
        
        const optimalNotice = document.getElementById("compress-optimal-notice");
        const btnText = document.getElementById("compress-download-btn-text");

        if (job.already_optimal) {
          if (optimalNotice) {
            document.getElementById("compress-optimal-message").textContent = job.message || "This file is already efficiently compressed. Compression could not reduce it further.";
            optimalNotice.style.display = "flex";
          }
          document.getElementById("metric-saved-pct").textContent = "0%";
          if (btnText) btnText.textContent = "Download Original PDF";
        } else {
          if (optimalNotice) optimalNotice.style.display = "none";
          let savedPct = 0;
          if (job.original_size_mb > 0 && job.compressed_size_mb !== null) {
            savedPct = Math.max(0, Math.round((1 - job.compressed_size_mb / job.original_size_mb) * 100));
          }
          document.getElementById("metric-saved-pct").textContent = `${savedPct}%`;
          if (btnText) btnText.textContent = "Download Compressed PDF";
        }

        const dlBtn = document.getElementById("compress-download-btn");
        dlBtn.href = job.download_url;
        document.getElementById("compress-result").style.display = "block";
        document.getElementById("btn-compress-submit").disabled = false;
      } else if (job.status === "error") {
        clearInterval(compressPollInterval);
        progressBox.style.display = "none";
        document.getElementById("btn-compress-submit").disabled = false;
        showError("compress", job.error_message || "An error occurred during compression.");
      }
    } catch (e) {
      clearInterval(compressPollInterval);
      document.getElementById("compress-progress").style.display = "none";
      document.getElementById("btn-compress-submit").disabled = false;
      showError("compress", e.message);
    }
  }, 1000);
}

// ==========================================
// Feature 2: Watermark Removal Logic
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
  loading.style.display = "block";

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
      document.getElementById("wm-scanned-warning").style.display = "flex";
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

    const typeBadge = cand.type.toUpperCase();
    const sample = cand.sample_text ? `"${cand.sample_text}"` : (cand.type === "image" ? "Image Logo / Stamp" : "Vector Art Stamp");
    const previewUrl = cand.page_preview_image_url;
    const coverage = cand.estimated_coverage || `Confidence: ${Math.round(cand.confidence * 100)}%`;

    card.innerHTML = `
      <img class="candidate-preview-img" src="${previewUrl}" alt="Page 1 Preview">
      <div class="candidate-details">
        <span class="candidate-type">${typeBadge}</span>
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
  const progressPercent = document.getElementById("wm-progress-percent");
  const progressStatus = document.getElementById("wm-progress-status");

  progressBox.style.display = "block";
  progressBar.style.width = "0%";
  progressPercent.textContent = "0%";
  progressStatus.textContent = "Preparing surgical page cleaning...";

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
    const removalJobId = data.job_id;

    pollWatermarkStatus(removalJobId);
  } catch (err) {
    progressBox.style.display = "none";
    document.getElementById("btn-wm-remove").disabled = false;
    showError("wm", err.message);
  }
}

function pollWatermarkStatus(jobId) {
  if (wmPollInterval) clearInterval(wmPollInterval);

  wmPollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/watermark/status/${jobId}`);
      if (!res.ok) throw new Error("Unable to fetch removal progress.");

      const job = await res.json();
      const progressBox = document.getElementById("wm-progress");
      const progressBar = document.getElementById("wm-progress-bar");
      const progressPercent = document.getElementById("wm-progress-percent");
      const progressStatus = document.getElementById("wm-progress-status");

      if (job.status === "processing") {
        const pct = job.progress || 0;
        progressBar.style.width = `${pct}%`;
        progressPercent.textContent = `${pct}%`;
        progressStatus.textContent = `Cleaned page ${job.pages_cleaned} (${pct}% complete)...`;
      } else if (job.status === "done") {
        clearInterval(wmPollInterval);
        progressBar.style.width = "100%";
        progressPercent.textContent = "100%";
        progressStatus.textContent = "Finished!";
        setTimeout(() => { progressBox.style.display = "none"; }, 500);

        document.getElementById("wm-cleaned-summary").textContent = `Cleaned ${job.pages_cleaned} pages. Verification passed.`;
        const dlBtn = document.getElementById("wm-download-btn");
        dlBtn.href = job.download_url;
        document.getElementById("wm-result").style.display = "block";
        document.getElementById("btn-wm-remove").disabled = false;
      } else if (job.status === "error") {
        clearInterval(wmPollInterval);
        progressBox.style.display = "none";
        document.getElementById("btn-wm-remove").disabled = false;
        showError("wm", job.error_message || "Watermark removal failed.");
      }
    } catch (e) {
      clearInterval(wmPollInterval);
      document.getElementById("wm-progress").style.display = "none";
      document.getElementById("btn-wm-remove").disabled = false;
      showError("wm", e.message);
    }
  }, 1000);
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

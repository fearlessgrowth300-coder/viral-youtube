const state = {
  pollingTimer: null,
  lastRuns: [],
  runsSignature: "",
  submittingRun: false
};

const statusRow = document.querySelector("#statusRow");
const runForm = document.querySelector("#runForm");
const startButton = document.querySelector("#startButton");
const sampleButton = document.querySelector("#sampleButton");
const refreshButton = document.querySelector("#refreshButton");
const installAppButton = document.querySelector("#installAppButton");
const jobsList = document.querySelector("#jobsList");
const runsList = document.querySelector("#runsList");
const jobCount = document.querySelector("#jobCount");
const runCount = document.querySelector("#runCount");
const previewVideo = document.querySelector("#previewVideo");
const previewTitle = document.querySelector("#previewTitle");
const previewLink = document.querySelector("#previewLink");
const sourceInput = document.querySelector("#sourceInput");
const sourceAnalysis = document.querySelector("#sourceAnalysis");
const sourceThumb = document.querySelector("#sourceThumb");
const sourceTitle = document.querySelector("#sourceTitle");
const sourceMeta = document.querySelector("#sourceMeta");
const publishSetup = document.querySelector("#publishSetup");
const publishState = document.querySelector("#publishState");
const manualUploadModal = document.querySelector("#manualUploadModal");
const manualUploadClose = document.querySelector("#manualUploadClose");
const manualUploadFile = document.querySelector("#manualUploadFile");
const manualUploadTitleField = document.querySelector("#manualUploadTitleField");
const manualUploadDescription = document.querySelector("#manualUploadDescription");
const manualUploadTags = document.querySelector("#manualUploadTags");
const manualUploadOpenClip = document.querySelector("#manualUploadOpenClip");
const manualUploadDownload = document.querySelector("#manualUploadDownload");
let analyzeTimer = null;
let deferredInstallPrompt = null;

function pill(text, tone = "") {
  const span = document.createElement("span");
  span.className = `pill ${tone}`.trim();
  span.textContent = text;
  return span;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || response.statusText);
  }
  return payload;
}

function selectedPlatforms() {
  return [...document.querySelectorAll(".platforms input:checked")].map((item) => item.value);
}

function selectedCaptionStyle() {
  return document.querySelector('input[name="captionStyle"]:checked')?.value || "karaoke";
}

function selectCaptionPreset(card) {
  const input = card.querySelector('input[name="captionStyle"]');
  if (!input) {
    return;
  }
  const changed = !input.checked;
  input.checked = true;
  if (changed) {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }
  syncCaptionPresetCards();
}

function syncCaptionPresetCards() {
  document.querySelectorAll(".preset-card").forEach((card) => {
    const input = card.querySelector('input[name="captionStyle"]');
    const selected = Boolean(input?.checked);
    card.classList.toggle("is-selected", selected);
    card.setAttribute("aria-checked", selected ? "true" : "false");
  });
}

function initCaptionPresetCards() {
  document.querySelectorAll(".preset-card").forEach((card) => {
    card.setAttribute("role", "radio");
    card.tabIndex = 0;
    card.addEventListener("click", () => selectCaptionPreset(card));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectCaptionPreset(card);
      }
    });
    card.querySelector('input[name="captionStyle"]')?.addEventListener("change", syncCaptionPresetCards);
  });
  syncCaptionPresetCards();
}

function installHelpMessage() {
  if (location.protocol !== "https:" && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
    return "Phone install needs HTTPS. You can still use the phone URL in the browser, or open an HTTPS tunnel link and tap Install app again.";
  }
  return "If the install prompt does not appear, use your browser menu and choose Add to Home Screen.";
}

function initPwaInstall() {
  if ("serviceWorker" in navigator && window.isSecureContext) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/service-worker.js").catch(() => {});
    });
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredInstallPrompt = event;
  });

  window.addEventListener("appinstalled", () => {
    deferredInstallPrompt = null;
    if (installAppButton) {
      installAppButton.hidden = true;
    }
  });

  installAppButton?.addEventListener("click", async () => {
    if (!deferredInstallPrompt) {
      window.alert(installHelpMessage());
      return;
    }
    deferredInstallPrompt.prompt();
    await deferredInstallPrompt.userChoice.catch(() => {});
    deferredInstallPrompt = null;
  });
}

function compactError(value) {
  const clean = String(value || "").replace(/\s+/g, " ").trim();
  if (!clean) {
    return "";
  }
  if (clean.includes("Error parsing") && clean.includes("filter_complex")) {
    return "Render failed because FFmpeg could not parse a caption or hook overlay. Try again with the updated renderer.";
  }
  if (clean.includes("Connection error")) {
    return "Connection error while preparing media or voiceover.";
  }
  return clean.length > 260 ? `${clean.slice(0, 257)}...` : clean;
}

function cleanDisplayText(value) {
  let text = String(value || "")
    .replace(/^Viral score\s+[\d.]+:\s*/i, "")
    .replace(/\[[^\]]+\]/g, " ")
    .replace(/\([^)]*\)/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const words = text.split(" ");
  const collapsed = [];
  for (const word of words) {
    const clean = word.toLowerCase().replace(/[^a-z0-9]+/g, "");
    const previous = collapsed.length ? collapsed[collapsed.length - 1].toLowerCase().replace(/[^a-z0-9]+/g, "") : "";
    if (clean && clean === previous) {
      continue;
    }
    collapsed.push(word);
  }
  return collapsed.join(" ").trim();
}

function uniqueTags(tags) {
  return [...new Set(
    tags
      .map((tag) => String(tag || "").trim().replace(/^#/, "").replace(/\s+/g, ""))
      .filter(Boolean)
  )];
}

function manualUploadMetadata(clip) {
  const candidate = clip.candidate || {};
  const tags = uniqueTags(candidate.tags?.length ? candidate.tags : ["shorts", "viral", candidate.kind || "highlight"]);
  const hashtags = tags.slice(0, 8).map((tag) => `#${tag}`).join(" ");
  const description = [
    cleanDisplayText(candidate.hook || candidate.caption || clip.title || ""),
    cleanDisplayText(candidate.description || candidate.reason || ""),
    hashtags,
    clip.has_ai_voiceover ? "Disclosure: contains AI-generated voiceover." : ""
  ].filter(Boolean).join("\n\n");
  return {
    file: clip.video_path || "",
    title: cleanDisplayText(candidate.title || clip.title || "Clip").slice(0, 100),
    description,
    tags: tags.join(", ")
  };
}

async function copyText(value) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const helper = document.createElement("textarea");
  helper.value = value;
  helper.setAttribute("readonly", "");
  helper.style.position = "fixed";
  helper.style.left = "-9999px";
  document.body.append(helper);
  helper.select();
  document.execCommand("copy");
  helper.remove();
}

function isYouTubeSource(value) {
  try {
    const url = new URL(value);
    return /(^|\.)youtube\.com$|(^|\.)youtu\.be$/.test(url.hostname);
  } catch {
    return false;
  }
}

function scheduleSourceAnalysis() {
  clearTimeout(analyzeTimer);
  const sourceValue = sourceInput.value.trim();
  if (!sourceValue || !isYouTubeSource(sourceValue)) {
    sourceAnalysis.hidden = true;
    return;
  }
  sourceMeta.textContent = "Analyzing link...";
  sourceTitle.textContent = sourceValue;
  sourceThumb.removeAttribute("src");
  sourceAnalysis.hidden = false;
  analyzeTimer = setTimeout(() => analyzeSource(sourceValue), 600);
}

async function analyzeSource(sourceValue) {
  try {
    const payload = await api(`/api/analyze?source=${encodeURIComponent(sourceValue)}`);
    const analysis = payload.analysis;
    sourceTitle.textContent = analysis.title || sourceValue;
    sourceMeta.textContent = analysis.analysis_error
      ? `YouTube metadata unavailable - ${analysis.analysis_error}`
      : [
          analysis.is_live ? "LIVE source" : "",
          analysis.duration_string || "",
          analysis.has_captions ? "captions found" : "no captions found"
        ].filter(Boolean).join(" - ");
    if (analysis.thumbnail) {
      sourceThumb.src = analysis.thumbnail;
    }
  } catch (error) {
    sourceTitle.textContent = "Could not analyze link";
    sourceMeta.textContent = error.message;
  }
}

function renderStatus(status) {
  statusRow.replaceChildren(
    pill(status.openai_configured ? "OpenAI ready" : "OpenAI off", status.openai_configured ? "ok" : "warn"),
    pill(status.publish_enabled ? "Posting on" : "Posting locked", status.publish_enabled ? "ok" : "warn"),
    pill(status.youtube_download_allowed ? "YouTube global on" : "YouTube needs permission", status.youtube_download_allowed ? "ok" : "warn")
  );

  if (status.latest_clip?.video_url) {
    const currentSrc = previewVideo.dataset.src || "";
    const nextSrc = status.latest_clip.video_url;
    if (currentSrc !== nextSrc && previewVideo.paused) {
      previewVideo.src = nextSrc;
      previewVideo.dataset.src = nextSrc;
      previewTitle.textContent = status.latest_clip.title || "Latest clip";
      previewLink.href = nextSrc;
    }
  }
  renderPublishSetup(status.publish_setup);
}

function renderPublishSetup(setup) {
  if (!setup || !publishSetup) {
    return;
  }
  publishState.textContent = setup.enabled ? "enabled" : "locked";
  const platforms = [
    ["YouTube", setup.youtube],
    ["TikTok", setup.tiktok],
    ["Instagram", setup.instagram]
  ];
  const nodes = platforms.map(([name, platform]) => {
    const card = document.createElement("article");
    card.className = `publish-card ${platform.ready ? "ready" : "blocked"}`;
    const title = document.createElement("strong");
    title.textContent = name;
    const status = document.createElement("span");
    status.className = `pill ${platform.ready ? "ok" : "warn"}`;
    status.textContent = platform.ready ? "ready" : "blocked";
    const message = document.createElement("p");
    message.textContent = platform.message || "";
    card.append(title, status, message);
    return card;
  });
  publishSetup.replaceChildren(...nodes);
}

function renderJobs(jobs) {
  jobCount.textContent = String(jobs.length);
  if (!jobs.length) {
    jobsList.innerHTML = '<div class="empty">No active jobs</div>';
    return;
  }
  const template = document.querySelector("#jobTemplate");
  const nodes = jobs.map((job) => {
    const node = template.content.cloneNode(true);
    node.querySelector('[data-field="message"]').textContent = job.message || job.status;
    node.querySelector('[data-field="source"]').textContent = job.error ? compactError(job.error) : job.source;
    const progress = Math.max(0, Math.min(100, Number(job.progress || 0)));
    node.querySelector('[data-field="bar"]').style.width = `${progress}%`;
    node.querySelector('[data-field="progress-label"]').textContent = `${progress}% - ${job.phase || job.status}`;
    const status = node.querySelector('[data-field="status"]');
    status.textContent = job.status;
    status.classList.add(
      job.status === "succeeded" ? "ok" : ["failed", "cancelled"].includes(job.status) ? "error" : "warn"
    );
    const stop = node.querySelector('[data-action="cancel-job"]');
    const canStop = ["queued", "running", "cancelling"].includes(job.status);
    stop.disabled = !canStop || job.status === "cancelling";
    stop.textContent = job.status === "cancelling" ? "Stopping" : "Stop";
    stop.addEventListener("click", () => cancelJob(job.id).catch((error) => window.alert(error.message)));
    node
      .querySelector('[data-action="delete-job"]')
      .addEventListener("click", () => deleteJob(job).catch((error) => window.alert(error.message)));
    return node;
  });
  jobsList.replaceChildren(...nodes);
}

function runsSignature(runs) {
  return JSON.stringify(
    runs.map((run) => ({
      id: run.id,
      clip_count: run.clip_count,
      queue_path: run.queue_path,
      skipped: run.skipped || [],
      clips: run.clips.map((clip) => ({
        video_path: clip.video_path,
        title: clip.title,
        reason: clip.candidate?.reason || ""
      }))
    }))
  );
}

function renderRuns(runs) {
  state.lastRuns = runs;
  runCount.textContent = String(runs.length);
  if (!runs.length) {
    runsList.innerHTML = '<div class="empty">No rendered clips</div>';
    return;
  }

  const runTemplate = document.querySelector("#runTemplate");
  const clipTemplate = document.querySelector("#clipTemplate");
  const nodes = runs.map((run) => {
    const node = runTemplate.content.cloneNode(true);
    node.querySelector('[data-field="run"]').textContent = `${run.run_dir_label} - ${run.clip_count} clip(s)`;
    node.querySelector('[data-field="source"]').textContent = run.source;
    const publish = node.querySelector('[data-action="publish"]');
    publish.disabled = !run.queue_path || !run.clip_count;
    publish.addEventListener("click", () => publishRun(run.queue_path).catch((error) => window.alert(error.message)));
    node
      .querySelector('[data-action="delete-run"]')
      .addEventListener("click", () => deleteRun(run).catch((error) => window.alert(error.message)));

    const skipped = node.querySelector('[data-field="skipped"]');
    const skippedItems = run.skipped || [];
    if (skippedItems.length) {
      const title = document.createElement("strong");
      title.textContent = "Skipped clips";
      const list = document.createElement("ul");
      skippedItems.slice(0, 5).forEach((item) => {
        const entry = document.createElement("li");
        entry.textContent = compactError(item);
        list.append(entry);
      });
      skipped.replaceChildren(title, list);
    } else {
      skipped.remove();
    }

    const grid = node.querySelector('[data-field="clips"]');
    const clipNodes = run.clips.map((clip) => {
      const clipNode = clipTemplate.content.cloneNode(true);
      const video = clipNode.querySelector("video");
      video.src = clip.video_url;
      video.dataset.src = clip.video_url;
      video.preload = "metadata";
      clipNode.querySelector("h4").textContent = cleanDisplayText(clip.title);
      clipNode.querySelector("p").textContent = cleanDisplayText(clip.candidate?.description || clip.candidate?.reason || "");
      const link = clipNode.querySelector("a");
      link.href = clip.video_url;
      link.textContent = "Open clip";
      const download = clipNode.querySelector('[data-field="download"]');
      download.href = clip.video_url;
      download.setAttribute("download", "");
      const metadata = clipNode.querySelector('[data-field="metadata"]');
      metadata.href = clip.metadata_url || clip.video_url;
      const tags = clip.candidate?.tags || [];
      clipNode.querySelector(".clip-tags").textContent = tags.length ? tags.map((tag) => `#${tag}`).join(" ") : "";
      clipNode
        .querySelector('[data-action="manual-upload"]')
        .addEventListener("click", () => openManualUpload(clip));
      clipNode
        .querySelector('[data-action="delete-clip"]')
        .addEventListener("click", () => deleteClip(run, clip).catch((error) => window.alert(error.message)));
      return clipNode;
    });
    grid.replaceChildren(...clipNodes);
    return node;
  });
  runsList.replaceChildren(...nodes);
}

async function refreshAll() {
  const [status, jobs, runs] = await Promise.all([
    api("/api/status"),
    api("/api/jobs"),
    api("/api/runs")
  ]);
  renderStatus(status);
  renderJobs(jobs.jobs);
  const nextRunsSignature = runsSignature(runs.runs);
  if (nextRunsSignature !== state.runsSignature) {
    state.runsSignature = nextRunsSignature;
    renderRuns(runs.runs);
  } else {
    runCount.textContent = String(runs.runs.length);
  }
}

function ensurePolling() {
  if (!state.pollingTimer) {
    state.pollingTimer = setInterval(() => {
      refreshAll().catch(() => {});
    }, 2500);
  }
}

function setRunSubmitting(submitting) {
  state.submittingRun = submitting;
  startButton.disabled = submitting;
  sampleButton.disabled = submitting;
  startButton.setAttribute("aria-busy", submitting ? "true" : "false");
}

async function startRun(useSample = false) {
  if (state.submittingRun) {
    return;
  }
  setRunSubmitting(true);
  try {
    const sourceValue = document.querySelector("#sourceInput").value.trim();
    const youtubeAllowInput = document.querySelector("#youtubeAllowInput");
    if (!useSample && isYouTubeSource(sourceValue) && !youtubeAllowInput.checked) {
      const confirmed = window.confirm(
        "Only continue if you own this YouTube video/live stream or have permission to clip it. Continue?"
      );
      if (!confirmed) {
        return;
      }
      youtubeAllowInput.checked = true;
    }

    const payload = {
      use_sample: useSample,
      source: sourceValue,
      transcript: document.querySelector("#transcriptInput").value,
      clips: document.querySelector("#clipsInput").value,
      clip_length: document.querySelector("#lengthInput").value,
      live_capture_minutes: document.querySelector("#liveCaptureInput").value,
      render_quality: document.querySelector("#qualityInput").value,
      clip_model: document.querySelector("#clipModelInput").value,
      genre: document.querySelector("#genreInput").value,
      caption_style: selectedCaptionStyle(),
      auto_hook: document.querySelector("#autoHookInput").checked,
      voiceover: document.querySelector("#voiceoverInput").checked,
      horizontal: document.querySelector("#horizontalInput").checked,
      allow_youtube_download: youtubeAllowInput.checked,
      platforms: selectedPlatforms()
    };
    await api("/api/run", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    await refreshAll();
    ensurePolling();
  } finally {
    setRunSubmitting(false);
  }
}

async function cancelJob(jobId) {
  await api(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    body: JSON.stringify({})
  });
  await refreshAll();
}

async function deleteJob(job) {
  if (["queued", "running", "cancelling"].includes(job.status)) {
    const confirmed = window.confirm("Delete this active job from the list and request it to stop?");
    if (!confirmed) {
      return;
    }
  }
  await api(`/api/jobs/${encodeURIComponent(job.id)}`, { method: "DELETE" });
  await refreshAll();
}

async function publishRun(queuePath) {
  const result = await api("/api/publish", {
    method: "POST",
    body: JSON.stringify({ queue_path: queuePath, approve: true })
  });
  if (result.publish_setup) {
    renderPublishSetup(result.publish_setup);
  }
  const summary = result.results
    .map((item) => `${item.platform}: ${item.status}${item.message ? ` - ${item.message}` : ""}${item.url ? `\n${item.url}` : ""}`)
    .join("\n\n");
  window.alert(summary || "No queue entries");
}

async function deleteRun(run) {
  const confirmed = window.confirm(
    `Delete this run and all ${run.clip_count} generated clip(s)? This cannot be undone.`
  );
  if (!confirmed) {
    return;
  }
  await api("/api/delete", {
    method: "POST",
    body: JSON.stringify({ type: "run", run_dir: run.run_dir })
  });
  state.runsSignature = "";
  await refreshAll();
}

async function deleteClip(run, clip) {
  const confirmed = window.confirm(`Delete "${clip.title}"? This cannot be undone.`);
  if (!confirmed) {
    return;
  }
  await api("/api/delete", {
    method: "POST",
    body: JSON.stringify({ type: "clip", run_dir: run.run_dir, video_path: clip.video_path })
  });
  state.runsSignature = "";
  await refreshAll();
}

function openManualUpload(clip) {
  const metadata = manualUploadMetadata(clip);
  manualUploadFile.value = metadata.file;
  manualUploadTitleField.value = metadata.title;
  manualUploadDescription.value = metadata.description;
  manualUploadTags.value = metadata.tags;
  manualUploadOpenClip.href = clip.video_url;
  manualUploadDownload.href = clip.video_url;
  manualUploadDownload.setAttribute("download", "");
  manualUploadModal.hidden = false;
  window.open("https://studio.youtube.com", "_blank", "noopener,noreferrer");
}

function closeManualUpload() {
  manualUploadModal.hidden = true;
}

runForm.addEventListener("submit", (event) => {
  event.preventDefault();
  startRun(false).catch((error) => window.alert(error.message));
});

sampleButton.addEventListener("click", () => {
  startRun(true).catch((error) => window.alert(error.message));
});

refreshButton.addEventListener("click", () => {
  refreshAll().catch((error) => window.alert(error.message));
});

manualUploadClose.addEventListener("click", closeManualUpload);
manualUploadModal.addEventListener("click", (event) => {
  if (event.target === manualUploadModal) {
    closeManualUpload();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !manualUploadModal.hidden) {
    closeManualUpload();
  }
});
document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const target = document.querySelector(`#${button.dataset.copyTarget}`);
    await copyText(target.value);
    const original = button.textContent;
    button.textContent = "Copied";
    setTimeout(() => {
      button.textContent = original;
    }, 900);
  });
});

sourceInput.addEventListener("input", scheduleSourceAnalysis);
sourceInput.addEventListener("paste", () => setTimeout(scheduleSourceAnalysis, 0));

initCaptionPresetCards();
initPwaInstall();
refreshAll().catch((error) => {
  statusRow.replaceChildren(pill(error.message, "error"));
});
ensurePolling();

const storageKey = "sales-retro-backend-config";

const state = {
  sessionId: "",
  mediaRecorder: null,
  startedAt: 0,
  timer: null,
  chunks: 0,
  lastUploadedAudio: null,
};

const $ = (id) => document.getElementById(id);

function getConfig() {
  return {
    prompt: $("promptInput").value,
    uploadIntervalSeconds: Number($("intervalInput").value || 60),
    coachEngine: $("coachEngineInput").value,
    deepseekApiKey: $("deepseekKeyInput").value,
    deepseekBaseUrl: $("deepseekBaseUrlInput").value,
    deepseekModel: $("deepseekModelInput").value,
    volcAsrApiKey: $("volcKeyInput").value,
    volcAsrResourceId: $("volcResourceInput").value,
    volcAsrWsUrl: $("volcWsInput").value,
    volcAsrLanguage: "zh-CN",
    meetingDurationMinutes: 90,
    charsPerStep: 800,
  };
}

function setConfig(config) {
  $("promptInput").value = config.prompt || "";
  $("intervalInput").value = String(config.uploadIntervalSeconds || 60);
  $("coachEngineInput").value = config.coachEngine || "llm";
  $("deepseekKeyInput").value = config.deepseekApiKey || "";
  $("deepseekBaseUrlInput").value = config.deepseekBaseUrl || "https://api.deepseek.com";
  $("deepseekModelInput").value = config.deepseekModel || "deepseek-v4-pro";
  $("volcKeyInput").value = config.volcAsrApiKey || "";
  $("volcResourceInput").value = config.volcAsrResourceId || "volc.seedasr.sauc.duration";
  $("volcWsInput").value = config.volcAsrWsUrl || "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async";
}

async function api(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.message || data.error || "请求失败");
  }
  return data;
}

async function ensureSession() {
  if (state.sessionId) return state.sessionId;
  const session = await api("/api/sessions", { config: getConfig() });
  state.sessionId = session.sessionId;
  $("sessionLabel").textContent = session.sessionId;
  return state.sessionId;
}

async function logEvent(type, data) {
  await ensureSession();
  await api("/api/events", { sessionId: state.sessionId, type, data });
  await refreshLog();
}

function saveConfig() {
  localStorage.setItem(storageKey, JSON.stringify(getConfig()));
}

function loadConfig() {
  const raw = localStorage.getItem(storageKey);
  if (raw) setConfig(JSON.parse(raw));
}

async function loadDefaultConfig() {
  const response = await fetch("/api/default-config");
  const defaults = await response.json();
  const raw = localStorage.getItem(storageKey);
  const saved = raw ? JSON.parse(raw) : {};
  setConfig({ ...defaults, ...stripEmptyValues(saved) });
  saveConfig();
}

function stripEmptyValues(config) {
  return Object.fromEntries(Object.entries(config).filter(([, value]) => value !== "" && value !== null));
}

function setUploadStatus(message) {
  $("uploadStatus").textContent = message;
}

function setRecordStatus(message) {
  $("recordStatus").textContent = message;
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function pickRecordingMimeType() {
  if (!window.MediaRecorder) return "";
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  return candidates.find((item) => MediaRecorder.isTypeSupported(item)) || "";
}

async function startRecording() {
  await ensureSession();
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    throw new Error("当前浏览器不支持录音。");
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const mimeType = pickRecordingMimeType();
  state.mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  state.startedAt = Date.now();
  state.chunks = 0;
  $("chunkCount").textContent = "0";
  $("recordButton").textContent = "停止录音";
  $("recordButton").classList.add("recording");
  setRecordStatus("录音中");

  state.mediaRecorder.addEventListener("dataavailable", async (event) => {
    if (!event.data || event.data.size === 0) return;
    state.chunks += 1;
    $("chunkCount").textContent = String(state.chunks);
    const audioBase64 = await blobToDataUrl(event.data);
    await api("/api/audio-chunk", {
      sessionId: state.sessionId,
      chunkIndex: state.chunks,
      fileName: `chunk_${String(state.chunks).padStart(4, "0")}.webm`,
      mimeType: event.data.type,
      audioBase64,
    });
    await refreshLog();
  });

  state.mediaRecorder.addEventListener("stop", () => {
    stream.getTracks().forEach((track) => track.stop());
  });

  const intervalMs = Math.max(1, getConfig().uploadIntervalSeconds) * 1000;
  state.mediaRecorder.start(intervalMs);
  state.timer = setInterval(updateDuration, 1000);
  await logEvent("recording_started", { intervalSeconds: getConfig().uploadIntervalSeconds });
}

async function stopRecording() {
  if (!state.mediaRecorder) return;
  state.mediaRecorder.stop();
  state.mediaRecorder = null;
  clearInterval(state.timer);
  $("recordButton").textContent = "开始录音";
  $("recordButton").classList.remove("recording");
  setRecordStatus("录音已停止");
  await logEvent("recording_stopped", { durationSeconds: Math.round((Date.now() - state.startedAt) / 1000) });
}

function updateDuration() {
  const seconds = Math.max(0, Math.floor((Date.now() - state.startedAt) / 1000));
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  $("recordDuration").textContent = `${mm}:${ss}`;
}

async function saveAudioFile() {
  await ensureSession();
  const file = $("audioFileInput").files[0];
  if (!file) throw new Error("请选择录音文件");
  setUploadStatus(`正在保存 ${file.name}，请稍等...`);
  const audioBase64 = await blobToDataUrl(file);
  const result = await api("/api/audio-upload", {
    sessionId: state.sessionId,
    fileName: file.name,
    mimeType: file.type,
    audioBase64,
  });
  state.lastUploadedAudio = result.path;
  await refreshLog();
  setUploadStatus(`已保存 ${file.name}，大小 ${(result.bytes / 1024 / 1024).toFixed(2)} MB。`);
  return result;
}

async function coachAudioFile() {
  // Disable the button on the very first click — BEFORE the (possibly multi-minute)
  // upload — so a frozen-looking save can't be re-clicked into concurrent runs.
  // The button stays disabled until this run returns (success, 提前终止, or error)
  // and is restored only in the outer finally.
  setCoachAudioRunning(true);
  try {
    if (!state.lastUploadedAudio) await saveAudioFile();
    setUploadStatus("正在转写并运行 Copilot。为保证完整性，上传录音会按真实音频时长推流...");
    const poller = setInterval(() => refreshLog().catch(console.error), 3000);
    try {
      const result = await api("/api/coach-upload", {
        sessionId: state.sessionId,
        path: state.lastUploadedAudio,
        config: getConfig(),
      });
      renderAlerts(result.alerts || []);
      await refreshLog();
      const prefix = result.cancelled ? "已提前终止" : "处理完成";
      setUploadStatus(`${prefix}：转写 ${result.transcript?.length || 0} 字，生成 ${result.alerts?.length || 0} 条提醒。`);
    } finally {
      clearInterval(poller);
    }
  } finally {
    setCoachAudioRunning(false);
  }
}

function setCoachAudioRunning(running) {
  $("coachAudioButton").disabled = running;
  $("saveAudioButton").disabled = running;
  const stopButton = $("stopCoachAudioButton");
  stopButton.hidden = !running;
  stopButton.disabled = false;
}

async function stopCoachAudioFile() {
  if (!state.sessionId) return;
  $("stopCoachAudioButton").disabled = true;
  setUploadStatus("正在提前终止，保留已转写部分...");
  await api("/api/coach-upload/cancel", { sessionId: state.sessionId });
}

async function coachTranscript() {
  await ensureSession();
  const result = await api("/api/coach-transcript", {
    sessionId: state.sessionId,
    transcript: $("transcriptInput").value,
    config: getConfig(),
  });
  renderAlerts(result.alerts || []);
  await refreshLog();
}

function renderAlerts(alerts) {
  $("alertCount").textContent = `${alerts.length} 条`;
  if (!alerts.length) {
    $("alerts").className = "alerts empty";
    $("alerts").textContent = "暂无提醒";
    return;
  }
  $("alerts").className = "alerts";
  $("alerts").innerHTML = alerts
    .map(
      (alert) => `
        <article class="alert-card">
          <strong>${alert.elapsedMinutes || 0} 分钟 · ${escapeHtml(alert.priority || "medium")} · ${escapeHtml(alert.type || "")}</strong>
          <p>${escapeHtml(alert.message || "")}</p>
          <p>${escapeHtml(alert.suggested_question || "")}</p>
          <small>${escapeHtml(alert.reason || "")}</small>
        </article>
      `,
    )
    .join("");
}

async function refreshLog() {
  if (!state.sessionId) return;
  const response = await fetch(`/api/logs/current?sessionId=${encodeURIComponent(state.sessionId)}`);
  const data = await response.json();
  const events = data.events || [];
  $("logOutput").textContent = JSON.stringify(events, null, 2);
  const realtimeAlerts = events.filter((event) => event.type === "coach_alert").map((event) => event.data);
  if (realtimeAlerts.length) renderAlerts(realtimeAlerts);
}

async function exportDiagnostics() {
  await ensureSession();
  window.location.href = `/api/diagnostics?sessionId=${encodeURIComponent(state.sessionId)}`;
}

async function clearLogs() {
  if (!state.sessionId) return;
  await api("/api/logs/clear", { sessionId: state.sessionId });
  state.sessionId = "";
  state.lastUploadedAudio = null;
  $("sessionLabel").textContent = "未开始";
  $("logOutput").textContent = "";
  renderAlerts([]);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function wireEvents() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".mode-panel").forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      $(`${tab.dataset.mode}Panel`).classList.add("active");
    });
  });

  $("saveConfigButton").addEventListener("click", () => {
    saveConfig();
    logEvent("config_saved", getConfig()).catch(showError);
  });
  $("openKeyModalButton").addEventListener("click", () => $("keyDialog").showModal());
  $("saveKeysButton").addEventListener("click", saveConfig);
  $("importPromptButton").addEventListener("click", () => $("promptFileInput").click());
  $("clearPromptButton").addEventListener("click", () => {
    $("promptInput").value = "";
    saveConfig();
  });
  $("promptFileInput").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    $("promptInput").value = await file.text();
    saveConfig();
  });
  $("audioFileInput").addEventListener("change", (event) => {
    const file = event.target.files[0];
    state.lastUploadedAudio = null;
    $("audioFileLabel").textContent = file ? `${file.name} - ${(file.size / 1024 / 1024).toFixed(2)} MB` : "选择录音文件";
    setUploadStatus(file ? "已选择文件，点击保存或直接运行 Copilot。" : "上传音频会保存到后台 `outputs/web_sessions`。");
  });
  $("recordButton").addEventListener("click", () => {
    (state.mediaRecorder ? stopRecording() : startRecording()).catch(showError);
  });
  $("saveAudioButton").addEventListener("click", () => saveAudioFile().catch(showError));
  $("coachAudioButton").addEventListener("click", () => coachAudioFile().catch(showError));
  $("stopCoachAudioButton").addEventListener("click", () => stopCoachAudioFile().catch(showError));
  $("coachTranscriptButton").addEventListener("click", () => coachTranscript().catch(showError));
  $("refreshLogButton").addEventListener("click", () => refreshLog().catch(showError));
  $("exportDiagButton").addEventListener("click", () => exportDiagnostics().catch(showError));
  $("clearLogButton").addEventListener("click", () => clearLogs().catch(showError));
}

function showError(error) {
  const message = error.message || String(error);
  setRecordStatus(message);
  setUploadStatus(message);
  console.error(error);
}

loadConfig();
loadDefaultConfig().catch(showError);
wireEvents();

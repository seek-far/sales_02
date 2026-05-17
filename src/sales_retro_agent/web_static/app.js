const storageKey = "sales-retro-static-config";
const logStorageKey = "sales-retro-static-log";

const defaultConfig = {
  prompt: "",
  uploadIntervalSeconds: 60,
  coachEngine: "rules",
  meetingDurationMinutes: 90,
  charsPerStep: 800,
};

const state = {
  sessionId: "",
  mediaRecorder: null,
  speechRecognition: null,
  startedAt: 0,
  timer: null,
  chunks: [],
  log: [],
  coachState: createCoachState(),
};

const $ = (id) => document.getElementById(id);

function createCoachState() {
  return {
    transcript: "",
    confirmed: new Set(),
    lastAlertByType: {},
    recentAlerts: [],
  };
}

function getConfig() {
  return {
    prompt: $("promptInput").value,
    uploadIntervalSeconds: Number($("intervalInput").value || 60),
    coachEngine: $("coachEngineInput").value,
    meetingDurationMinutes: 90,
    charsPerStep: 800,
  };
}

function setConfig(config) {
  $("promptInput").value = config.prompt || "";
  $("intervalInput").value = String(config.uploadIntervalSeconds || 60);
  $("coachEngineInput").value = "rules";
}

function saveConfig() {
  localStorage.setItem(storageKey, JSON.stringify(getConfig()));
  logEvent("config_saved", getConfig());
}

function loadConfig() {
  const raw = localStorage.getItem(storageKey);
  setConfig(raw ? { ...defaultConfig, ...JSON.parse(raw) } : defaultConfig);
}

function ensureSession() {
  if (state.sessionId) return state.sessionId;
  state.sessionId = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
  $("sessionLabel").textContent = state.sessionId;
  state.coachState = createCoachState();
  logEvent("session_started", { configSnapshot: getConfig() });
  return state.sessionId;
}

function logEvent(type, data = {}) {
  if (!state.sessionId && type !== "session_started") {
    ensureSession();
  }
  const event = {
    timestamp: new Date().toISOString(),
    type,
    data: sanitizeForLog(data),
  };
  state.log.push(event);
  localStorage.setItem(logStorageKey, JSON.stringify({ sessionId: state.sessionId, events: state.log }));
  refreshLog();
}

function sanitizeForLog(value) {
  if (!value || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map(sanitizeForLog);
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      key,
      /key|secret|token|password/i.test(key) ? maskSecret(String(item || "")) : sanitizeForLog(item),
    ]),
  );
}

function maskSecret(value) {
  if (!value) return "";
  return value.length <= 8 ? "***" : `${value.slice(0, 4)}...${value.slice(-4)}`;
}

function refreshLog() {
  $("logOutput").textContent = JSON.stringify(state.log, null, 2);
}

function clearLogs() {
  state.sessionId = "";
  state.log = [];
  state.chunks = [];
  state.coachState = createCoachState();
  localStorage.removeItem(logStorageKey);
  $("sessionLabel").textContent = "未开始";
  $("logOutput").textContent = "";
  $("chunkCount").textContent = "0";
  $("recordDuration").textContent = "00:00";
  $("downloadRecordingButton").disabled = true;
  renderAlerts([]);
}

function setUploadStatus(message) {
  $("uploadStatus").textContent = message;
}

function setRecordStatus(message) {
  $("recordStatus").textContent = message;
}

function pickRecordingMimeType() {
  if (!window.MediaRecorder) return "";
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  return candidates.find((item) => MediaRecorder.isTypeSupported(item)) || "";
}

async function startRecording() {
  ensureSession();
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    throw new Error("当前浏览器不支持本地录音。");
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const mimeType = pickRecordingMimeType();
  state.mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  state.startedAt = Date.now();
  state.chunks = [];
  $("chunkCount").textContent = "0";
  $("recordButton").textContent = "停止录音";
  $("recordButton").classList.add("recording");
  $("downloadRecordingButton").disabled = true;
  setRecordStatus("录音中");

  state.mediaRecorder.addEventListener("dataavailable", (event) => {
    if (!event.data || event.data.size === 0) return;
    state.chunks.push(event.data);
    $("chunkCount").textContent = String(state.chunks.length);
    logEvent("audio_chunk_captured", {
      chunkIndex: state.chunks.length,
      bytes: event.data.size,
      mimeType: event.data.type,
    });
  });

  state.mediaRecorder.addEventListener("stop", () => {
    stream.getTracks().forEach((track) => track.stop());
    $("downloadRecordingButton").disabled = state.chunks.length === 0;
  });

  const intervalMs = Math.max(1, getConfig().uploadIntervalSeconds) * 1000;
  state.mediaRecorder.start(intervalMs);
  state.timer = setInterval(updateDuration, 1000);
  startSpeechRecognition();
  logEvent("recording_started", { intervalSeconds: getConfig().uploadIntervalSeconds });
}

function stopRecording() {
  if (!state.mediaRecorder) return;
  state.mediaRecorder.stop();
  state.mediaRecorder = null;
  stopSpeechRecognition();
  clearInterval(state.timer);
  $("recordButton").textContent = "开始录音";
  $("recordButton").classList.remove("recording");
  setRecordStatus("录音已停止");
  logEvent("recording_stopped", { durationSeconds: Math.round((Date.now() - state.startedAt) / 1000) });
}

function startSpeechRecognition() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    $("recordMeta").textContent = "当前浏览器没有 Web Speech API。录音可下载，逐字稿需要手动粘贴。";
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "zh-CN";
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.onresult = (event) => {
    let finalText = "";
    let interimText = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const text = event.results[index][0]?.transcript || "";
      if (event.results[index].isFinal) finalText += text;
      else interimText += text;
    }
    if (finalText) {
      $("liveTranscriptInput").value = appendText($("liveTranscriptInput").value, finalText);
      evaluateRecentText(finalText, elapsedMinutes());
      logEvent("speech_final_text", { chars: finalText.length, preview: finalText.slice(0, 80) });
    }
    $("recordMeta").textContent = interimText || "正在监听语音。识别结果会保留在下方文本框。";
  };
  recognition.onerror = (event) => {
    $("recordMeta").textContent = `语音识别不可用：${event.error || "未知错误"}。你仍然可以下载录音并手动粘贴逐字稿。`;
  };
  recognition.onend = () => {
    if (state.mediaRecorder) recognition.start();
  };
  state.speechRecognition = recognition;
  recognition.start();
}

function stopSpeechRecognition() {
  if (!state.speechRecognition) return;
  state.speechRecognition.onend = null;
  state.speechRecognition.stop();
  state.speechRecognition = null;
}

function updateDuration() {
  const seconds = Math.max(0, Math.floor((Date.now() - state.startedAt) / 1000));
  $("recordDuration").textContent = formatDuration(seconds);
}

function elapsedMinutes() {
  if (!state.startedAt) return 1;
  return Math.max(1, Math.floor((Date.now() - state.startedAt) / 60000));
}

function formatDuration(seconds) {
  const mm = String(Math.floor(seconds / 60)).padStart(2, "0");
  const ss = String(seconds % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function downloadRecording() {
  if (!state.chunks.length) return;
  const mimeType = state.chunks[0].type || "audio/webm";
  const blob = new Blob(state.chunks, { type: mimeType });
  downloadBlob(blob, `sales-retro-${state.sessionId || "recording"}.webm`);
  logEvent("recording_downloaded", { bytes: blob.size, mimeType });
}

function handleAudioFileChange(event) {
  const file = event.target.files[0];
  $("audioFileLabel").textContent = file ? `${file.name} - ${(file.size / 1024 / 1024).toFixed(2)} MB` : "选择录音文件";
  setUploadStatus(
    file
      ? "已选择文件。纯前端版不会上传或转写音频，请用浏览器实时识别或粘贴已有逐字稿运行 Copilot。"
      : "纯前端版不会把音频上传到后台，也不能稳定调用本地火山 ASR。",
  );
  if (file) logEvent("audio_file_selected", { name: file.name, bytes: file.size, type: file.type });
}

function coachTranscript() {
  ensureSession();
  state.coachState = createCoachState();
  const transcript = $("transcriptInput").value.trim();
  if (!transcript) throw new Error("请先粘贴逐字稿。");
  const alerts = evaluateTranscript(transcript, getConfig());
  renderAlerts(alerts);
  logEvent("coach_transcript_completed", { transcriptChars: transcript.length, alerts: alerts.length });
}

function coachLiveTranscript() {
  ensureSession();
  state.coachState = createCoachState();
  const transcript = $("liveTranscriptInput").value.trim();
  if (!transcript) throw new Error("暂无可评估的实时逐字稿。");
  const alerts = evaluateTranscript(transcript, getConfig());
  renderAlerts(alerts);
  logEvent("coach_live_transcript_completed", { transcriptChars: transcript.length, alerts: alerts.length });
}

function evaluateTranscript(transcript, config) {
  const chunks = chunkText(transcript, config.charsPerStep || 800);
  const minutesPerStep = Math.max(1, Math.round((config.uploadIntervalSeconds || 60) / 60));
  const alerts = [];
  chunks.forEach((chunk, index) => {
    const alert = evaluateRecentText(chunk, Math.max(1, (index + 1) * minutesPerStep));
    if (alert) alerts.push(alert);
  });
  return alerts;
}

function evaluateRecentText(text, elapsedMinuteValue) {
  const alert = evaluateRules(state.coachState, text, elapsedMinuteValue, getConfig().meetingDurationMinutes);
  if (alert) {
    const payload = { elapsedMinutes: elapsedMinuteValue, ...alert };
    state.coachState.recentAlerts.push(payload);
    renderAlerts(state.coachState.recentAlerts);
    logEvent("coach_alert", payload);
    return payload;
  }
  return null;
}

function evaluateRules(coachState, newText, elapsedMinuteValue, meetingDurationMinutes) {
  const text = newText.trim();
  if (!text) return null;
  coachState.transcript = appendText(coachState.transcript, text);
  updateConfirmedFields(coachState, text);

  const candidates = [
    objectionUnhandled(text, elapsedMinuteValue),
    missingDiscovery(coachState, text, elapsedMinuteValue),
    qualificationGap(coachState, text, elapsedMinuteValue),
    nextStepDue(coachState, elapsedMinuteValue, meetingDurationMinutes),
    talkRatioOrMonologue(text, elapsedMinuteValue),
  ];

  for (const alert of candidates) {
    if (alert && canEmit(coachState, alert.type, elapsedMinuteValue)) {
      coachState.lastAlertByType[alert.type] = elapsedMinuteValue;
      return alert;
    }
  }
  return null;
}

function canEmit(coachState, type, elapsedMinuteValue) {
  const last = coachState.lastAlertByType[type];
  return last === undefined || elapsedMinuteValue - last >= 5;
}

function missingDiscovery(coachState, text, elapsedMinuteValue) {
  if (elapsedMinuteValue < 5 || coachState.confirmed.has("impact")) return null;
  if (!hasAny(text, ["痛", "慢", "延期", "麻烦", "问题", "不系统", "不一致", "担心"])) return null;
  return {
    priority: "high",
    type: "missing_discovery",
    message: "客户已经表达了问题，可以马上追问业务影响，先别急着讲功能。",
    reason: "近期逐字稿出现痛点信号，但尚未确认量化影响。",
    suggested_question: "这个问题现在对预测准确率、赢单率或团队时间大概造成了多大影响？",
  };
}

function qualificationGap(coachState, text, elapsedMinuteValue) {
  if (elapsedMinuteValue < 15) return null;
  if (!hasAny(text, ["试点", "方案", "采购", "推进", "上线", "合同"])) return null;
  const fields = [
    ["budget", "这类试点通常在什么预算范围内更容易推进？有没有审批门槛？"],
    ["authority", "除了您和周经理，还有谁会参与最终拍板或强影响这个决策？"],
    ["decision_process", "如果下周样本验证通过，后面从评审到合同大概要经过哪些步骤？"],
    ["timeline", "如果要赶上本季度或训练营，最晚什么时候需要确定？"],
  ];
  const missing = fields.find(([field]) => !coachState.confirmed.has(field));
  if (!missing) return null;
  return {
    priority: "high",
    type: "qualification_gap",
    message: "客户已有推进语境，但关键资格信息还没补齐。",
    reason: `当前还缺少 ${missing[0]} 信息。`,
    suggested_question: missing[1],
  };
}

function objectionUnhandled(text, elapsedMinuteValue) {
  if (elapsedMinuteValue < 3) return null;
  if (!hasAny(text, ["担心", "质疑", "贵", "安全", "出境", "准确率", "太长", "不用", "抵触"])) return null;
  return {
    priority: "medium",
    type: "objection_unhandled",
    message: "客户刚提出顾虑，建议先复述确认，再给处理路径。",
    reason: "近期逐字稿出现明显异议信号。",
    suggested_question: "我确认一下，您最担心的是准确率本身，还是主管和销售是否愿意采纳？",
  };
}

function nextStepDue(coachState, elapsedMinuteValue, meetingDurationMinutes) {
  if (elapsedMinuteValue < Math.max(20, Math.floor(meetingDurationMinutes * 0.7))) return null;
  if (coachState.confirmed.has("next_step")) return null;
  return {
    priority: "high",
    type: "next_step_due",
    message: "会议已进入后段，还没有明确下一步，建议现在收口。",
    reason: "已超过计划时长的 70%，但未检测到明确 next step。",
    suggested_question: "为了让这件事往前走，我们下次是否约一个样本评审会？谁需要一起参加，定在什么时候？",
  };
}

function talkRatioOrMonologue(text, elapsedMinuteValue) {
  if (elapsedMinuteValue < 8) return null;
  const salesMarkers = countMatches(text, ["销售", "顾问", "我这边"]);
  const customerMarkers = countMatches(text, ["客户", "王总", "周经理", "您这边"]);
  if (salesMarkers < 4 || customerMarkers > 0) return null;
  return {
    priority: "low",
    type: "talk_ratio_or_monologue",
    message: "销售连续讲得较多，可以停下来让客户确认。",
    reason: "近期窗口里销售侧发言明显多于客户侧。",
    suggested_question: "我先停一下，这部分和您现在的流程匹配吗？有没有哪里我理解偏了？",
  };
}

function updateConfirmedFields(coachState, text) {
  const checks = {
    impact: ["影响", "延期", "金额", "周期", "准确率", "赢单率", "缩到"],
    budget: ["预算", "报价", "价格", "审批门槛", "费用"],
    authority: ["拍板", "决策人", "总裁", "CFO", "王总", "sponsor"],
    decision_process: ["流程", "法务", "IT", "评审", "采购", "合同"],
    decision_criteria: ["标准", "准确率", "主管", "安全", "集成", "实施负担"],
    timeline: ["什么时候", "时间线", "月底", "下周", "训练营", "最晚"],
    next_step: ["下一步", "下次", "约", "日程", "会后", "明天", "下周二"],
  };
  Object.entries(checks).forEach(([field, keywords]) => {
    if (hasAny(text, keywords)) coachState.confirmed.add(field);
  });
}

function chunkText(text, charsPerStep) {
  const normalized = text.trim();
  if (!normalized) return [];
  const chunks = [];
  let current = [];
  let currentLength = 0;
  normalized.split(/\r?\n/).forEach((line) => {
    const lineLength = line.length + 1;
    if (current.length && currentLength + lineLength > charsPerStep) {
      chunks.push(current.join("\n").trim());
      current = [line];
      currentLength = lineLength;
    } else {
      current.push(line);
      currentLength += lineLength;
    }
  });
  if (current.length) chunks.push(current.join("\n").trim());
  return chunks;
}

function appendText(current, next) {
  return current ? `${current}\n${next}`.trim() : next.trim();
}

function hasAny(text, keywords) {
  return keywords.some((keyword) => text.includes(keyword));
}

function countMatches(text, keywords) {
  return keywords.reduce((total, keyword) => total + text.split(keyword).length - 1, 0);
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

function exportDiagnostics() {
  ensureSession();
  const payload = {
    sessionId: state.sessionId,
    exportedAt: new Date().toISOString(),
    config: sanitizeForLog(getConfig()),
    log: state.log,
    liveTranscript: $("liveTranscriptInput").value,
    transcript: $("transcriptInput").value,
  };
  downloadBlob(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }), `sales-retro-${state.sessionId}.json`);
  logEvent("diagnostics_exported", { events: state.log.length });
}

function downloadBlob(blob, fileName) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
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

  $("saveConfigButton").addEventListener("click", saveConfig);
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
  $("audioFileInput").addEventListener("change", handleAudioFileChange);
  $("recordButton").addEventListener("click", () => {
    try {
      state.mediaRecorder ? stopRecording() : startRecording().catch(showError);
    } catch (error) {
      showError(error);
    }
  });
  $("downloadRecordingButton").addEventListener("click", downloadRecording);
  $("coachLiveButton").addEventListener("click", () => {
    try {
      coachLiveTranscript();
    } catch (error) {
      showError(error);
    }
  });
  $("coachTranscriptButton").addEventListener("click", () => {
    try {
      coachTranscript();
    } catch (error) {
      showError(error);
    }
  });
  $("refreshLogButton").addEventListener("click", refreshLog);
  $("exportDiagButton").addEventListener("click", exportDiagnostics);
  $("clearLogButton").addEventListener("click", clearLogs);
}

function showError(error) {
  const message = error?.message || String(error);
  setRecordStatus(message);
  setUploadStatus(message);
  logEvent("error", { message });
  console.error(error);
}

loadConfig();
wireEvents();
refreshLog();

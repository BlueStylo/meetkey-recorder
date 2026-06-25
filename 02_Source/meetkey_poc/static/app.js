const appShell = document.getElementById("app");
const screen = document.getElementById("screen");
const homeButton = document.getElementById("homeButton");
const micDot = document.getElementById("micDot");
const micText = document.getElementById("micText");
const modal = document.getElementById("modal");
const modalTitle = document.getElementById("modalTitle");
const modalMessage = document.getElementById("modalMessage");
const modalActions = document.getElementById("modalActions");
const modalClose = document.getElementById("modalClose");
const modalCancel = document.getElementById("modalCancel");

let currentStatus = null;
let busy = false;
let renderKey = "";
let levelBusy = false;
let modalConfirm = null;
let deviceView = "home";
let selectedRecordId = null;
let selectedRecordPayload = null;
let recordsBusy = false;
let qrMode = "wifi";
let lastState = "";

function formatTime(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds || 0));
  const h = String(Math.floor(seconds / 3600)).padStart(2, "0");
  const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, "0");
  const s = String(seconds % 60).padStart(2, "0");
  return h === "00" ? `${m}:${s}` : `${h}:${m}:${s}`;
}

function qrUrl(data) {
  return `/qr.svg?data=${encodeURIComponent(data || "")}`;
}

function wifiCaption(wifiPayload) {
  const text = String(wifiPayload || "");
  const ssid = text.match(/(?:^|;)S:((?:\\.|[^;])*)/)?.[1] || "MeetKey";
  const password = text.match(/(?:^|;)P:((?:\\.|[^;])*)/)?.[1] || "";
  const clean = value => value.replace(/\\([\\;,:"])/g, "$1");
  return password ? `SSID ${clean(ssid)} · PW ${clean(password)}` : `SSID ${clean(ssid)}`;
}

function renderQrSwitcher(wifiPayload, linkUrl, label) {
  const hasLink = Boolean(linkUrl);
  const mode = qrMode === "link" && hasLink ? "link" : "wifi";
  const payload = mode === "link" ? linkUrl : wifiPayload;
  const step = mode === "link" ? "2/2 접속 링크" : "1/2 장비 연결";
  const hint = mode === "link" ? "MeetKey Wi-Fi 연결 후 스캔" : "먼저 MeetKey Wi-Fi에 연결";
  const caption = mode === "link" ? linkUrl : wifiCaption(wifiPayload);

  return `
    <div class="qr-stage" data-qr-mode="${mode}">
      <div class="qr-step">${escapeHtml(step)}</div>
      <img class="qr-image" src="${qrUrl(payload)}" alt="${escapeHtml(label)} QR" />
      <div class="qr-hint">${escapeHtml(hint)}</div>
      <div class="qr-nav" aria-label="QR 전환">
        <button type="button" data-action="qr-prev" ${mode === "wifi" ? "disabled" : ""}>이전</button>
        <button type="button" data-action="qr-next" ${mode === "link" || !hasLink ? "disabled" : ""}>다음</button>
      </div>
      <div class="url-text">${escapeHtml(caption || "")}</div>
    </div>
  `;
}

function renderQrPair(wifiPayload, linkUrl) {
  return `
    <div class="qr-pair" aria-label="저장된 회의 접속 QR">
      <div class="qr-card">
        <div class="qr-step">1/2 장비 연결</div>
        <img class="qr-image" src="${qrUrl(wifiPayload)}" alt="장비 연결 QR" />
        <div class="qr-hint">먼저 MeetKey Wi-Fi에 연결</div>
        <div class="url-text">${escapeHtml(wifiCaption(wifiPayload))}</div>
      </div>
      <div class="qr-card">
        <div class="qr-step">2/2 접속 링크</div>
        <img class="qr-image" src="${qrUrl(linkUrl)}" alt="접속 링크 QR" />
        <div class="qr-hint">연결 후 이 QR을 스캔</div>
        <div class="url-text">${escapeHtml(linkUrl || "접속 링크 준비 중")}</div>
      </div>
    </div>
  `;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return `${seconds}초`;
  if (minutes < 60) return `${minutes}분`;
  return `${Math.floor(minutes / 60)}시간 ${minutes % 60}분`;
}

function formatDate(value) {
  return String(value || "").replace("T", " ").slice(0, 16) || "날짜 확인 중";
}

function historyIcon() {
  return `
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M5 4.75h9.2L19 9.55v9.7H5V4.75Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
      <path d="M14 4.75v5h5" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>
      <path d="M8 13h8M8 16h5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
    </svg>
  `;
}

function setShellState(state) {
  appShell.classList.remove("is-idle", "is-recording", "is-paused", "is-processing-ready", "is-saving", "is-error");
  appShell.classList.add(`is-${state.replace("_", "-")}`);
  updateTopButton(state);
}

function updateTopButton(state = currentStatus?.state || "idle") {
  const showButton = state === "processing_ready" || deviceView !== "home";
  homeButton.classList.toggle("hidden", !showButton);
  homeButton.textContent = deviceView === "recordDetail" ? "뒤로" : "메인으로";
}

function setMic(status) {
  const mic = status.microphone || {};
  micDot.classList.toggle("ready", Boolean(mic.present));
  micDot.classList.toggle("missing", !mic.present);
  micText.textContent = `마이크: ${mic.name || "확인 중"}`;
}

function render(status) {
  currentStatus = status;
  const stateChanged = lastState && lastState !== status.state;
  lastState = status.state;
  if (stateChanged && (status.state === "idle" || status.state === "processing_ready")) {
    qrMode = "wifi";
  }

  if (status.state !== "idle" && deviceView !== "home") {
    deviceView = "home";
    selectedRecordId = null;
    selectedRecordPayload = null;
    qrMode = "wifi";
    renderKey = "";
  }
  setShellState(status.state);
  setMic(status);

  if (status.state === "idle" && deviceView !== "home") {
    updateTopButton(status.state);
    return;
  }

  const nextKey = [
    status.state,
    status.session_id || "",
    status.history_url || "",
    status.current_session_url || "",
    qrMode,
  ].join("|");

  if (nextKey !== renderKey) {
    renderKey = nextKey;
    if (status.state === "idle") renderIdle(status);
    else if (status.state === "recording") renderRecording(status);
    else if (status.state === "paused") renderPaused(status);
    else if (status.state === "saving") renderSaving();
    else if (status.state === "processing_ready") renderProcessing(status);
    else renderError(status);
  }

  updateDynamicText(status);
}

function renderIdle(status) {
  screen.innerHTML = `
    <section class="idle-layout">
      <button class="start-button" type="button" data-action="start">
        <span class="record-symbol" aria-hidden="true"></span>
        <span>녹음 시작</span>
      </button>
      <aside class="history-panel">
        <div class="history-title">${historyIcon()}<span>녹음 기록</span></div>
        <p class="history-hint">휴대폰으로 확인</p>
        <button class="history-open" type="button" data-action="records">목록 보기</button>
        ${renderQrSwitcher(status.wifi_qr_payload, status.history_url, "녹음 기록")}
      </aside>
    </section>
  `;
}

function renderRecordList(items) {
  const rows = items.map(item => `
    <button class="record-row" type="button" data-record-id="${escapeHtml(item.session_id)}">
      <strong>${escapeHtml(item.title || "제목 생성 중")}</strong>
      <span>${escapeHtml(formatDate(item.created_at))} · ${escapeHtml(formatDuration(item.elapsed_seconds))} · ${escapeHtml(statusLabel(item.status))}</span>
    </button>
  `).join("");

  screen.innerHTML = `
    <section class="records-layout">
      <div class="view-head">
        <button class="back-button" type="button" data-action="home">←</button>
        <div>
          <h1>녹음 기록</h1>
          <p>${items.length}개 기록</p>
        </div>
      </div>
      <div class="records-list">
        ${items.length ? rows : `<div class="empty-records">아직 저장된 녹음이 없습니다.</div>`}
      </div>
    </section>
  `;
}

function renderRecordDetail(payload) {
  const record = payload.record || {};
  const url = record.record_url || "";
  screen.innerHTML = `
    <section class="record-detail-layout">
      <div class="view-head">
        <button class="back-button" type="button" data-action="back-records">←</button>
        <div>
          <h1>${escapeHtml(record.title || "제목 생성 중")}</h1>
          <p>${escapeHtml(formatDate(record.created_at))} · ${escapeHtml(formatDuration(record.elapsed_seconds))}</p>
        </div>
      </div>
      <div class="record-qr-panel">
        ${renderQrPair(record.wifi_qr_payload, url)}
        <div class="record-detail-meta">
          <strong>${escapeHtml(statusLabel(record.status))}</strong>
          <span>${escapeHtml(record.status_label || "왼쪽 QR로 연결한 뒤 오른쪽 QR로 여세요.")}</span>
        </div>
      </div>
    </section>
  `;
}

function renderRecordsLoading(label = "기록 불러오는 중") {
  screen.innerHTML = `
    <section class="state-layout">
      <div class="state-label">${escapeHtml(label)}</div>
      <div class="timer">잠시만요</div>
    </section>
  `;
}

function renderRecording(status) {
  screen.innerHTML = `
    <section class="state-layout">
      <div class="state-label">녹음중</div>
      <div id="dynamicTimer" class="timer">${formatTime(status.elapsed_seconds)}</div>
      ${audioMeter()}
      <div class="controls">
        <button class="action-button pause" type="button" data-action="pause">일시정지</button>
        <button class="action-button primary" type="button" data-action="finish">저장</button>
      </div>
    </section>
  `;
}

function renderPaused(status) {
  screen.innerHTML = `
    <section class="state-layout">
      <div class="state-label">일시정지</div>
      <div id="dynamicTimer" class="timer">${formatTime(status.elapsed_seconds)}</div>
      ${audioMeter()}
      <div class="controls">
        <button class="action-button" type="button" data-action="resume">재개</button>
        <button class="action-button primary" type="button" data-action="finish">저장</button>
        <button class="action-button danger" type="button" data-action="cancel">녹음 취소</button>
      </div>
    </section>
  `;
}

function renderSaving() {
  screen.innerHTML = `
    <section class="state-layout">
      <div class="state-label">저장중</div>
      <div class="timer">잠시만요</div>
    </section>
  `;
}

function audioMeter() {
  return `
    <div class="level-panel">
      <div id="levelMeter" class="level-meter" aria-label="마이크 입력 레벨">
        ${Array.from({ length: 14 }, () => "<span></span>").join("")}
      </div>
      <div id="levelText" class="level-text">입력 대기중</div>
    </div>
  `;
}

function renderProcessing(status) {
  const seconds = status.expires_in ?? 0;
  screen.innerHTML = `
    <section class="processing-layout">
      <div class="processing-copy">
        <h1>현재 회의 QR</h1>
        <p>휴대폰에서 전사와 요약 진행 상황을 확인하세요.</p>
        <div id="countdownText" class="countdown">${formatTime(seconds)} 후 메인 화면으로 이동</div>
      </div>
      <div class="processing-qr">
        ${renderQrSwitcher(status.wifi_qr_payload, status.current_session_url, "현재 회의")}
      </div>
    </section>
  `;
}

function statusLabel(status) {
  const labels = {
    recording: "녹음 중",
    paused: "일시정지",
    saving: "저장 중",
    uploading: "전송 중",
    upload_failed: "전송 실패",
    uploaded: "전송 완료",
    queued: "처리 대기",
    transcribing: "전사 중",
    summarizing: "요약 중",
    ready: "완료",
    error: "오류",
    missing: "없음",
  };
  return labels[status] || status || "확인 중";
}

function updateDynamicText(status) {
  const timer = document.getElementById("dynamicTimer");
  if (timer && (status.state === "recording" || status.state === "paused")) {
    timer.textContent = formatTime(status.elapsed_seconds);
  }

  updateAudioMeter(status);

  const countdown = document.getElementById("countdownText");
  if (countdown && status.state === "processing_ready") {
    countdown.textContent = `${formatTime(status.expires_in ?? 0)} 후 메인 화면으로 이동`;
  }
}

function updateAudioMeter(status) {
  const meter = document.getElementById("levelMeter");
  if (!meter) return;

  const audio = status.audio_level || {};
  const level = status.state === "recording" ? Number(audio.level || 0) : 0;
  const peak = status.state === "recording" ? Number(audio.peak || 0) : 0;
  const active = status.state === "recording" && Boolean(audio.active);
  const now = Date.now() / 180;
  const bars = meter.querySelectorAll("span");

  bars.forEach((bar, index) => {
    const centerBias = 1 - Math.abs(index - (bars.length - 1) / 2) / bars.length;
    const motion = 0.55 + Math.abs(Math.sin(now + index * 0.77)) * 0.45;
    const height = Math.min(1, 0.07 + level * motion * (0.45 + centerBias) + peak * 0.12);
    bar.style.transform = `scaleY(${height.toFixed(3)})`;
    bar.classList.toggle("active", active);
  });

  const text = document.getElementById("levelText");
  if (text) {
    if (status.state === "paused") text.textContent = "입력 일시정지";
    else text.textContent = active ? "음성 입력 감지중" : "입력 대기중";
  }
}

function renderError(status) {
  screen.innerHTML = `
    <section class="state-layout">
      <div class="state-label">오류</div>
      <p>${status.last_error || "처리 중 문제가 발생했습니다."}</p>
      <button class="action-button primary" type="button" data-action="reset">메인으로</button>
    </section>
  `;
}

async function api(action) {
  if (busy) return;
  busy = true;
  try {
    const response = await fetch(`/api/${action}`, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) {
      showModal("알림", payload.error || "요청을 처리할 수 없습니다.");
      if (payload.status) render(payload.status);
      return;
    }
    render(payload);
  } catch (error) {
    showModal("연결 오류", "MeetKey 앱과 통신할 수 없습니다.");
  } finally {
    busy = false;
  }
}

async function refresh() {
  try {
    const response = await fetch("/api/status");
    const payload = await response.json();
    render(payload);
  } catch (error) {
    showModal("연결 오류", "MeetKey 앱 상태를 읽을 수 없습니다.");
  }
}

async function refreshAudioLevel() {
  if (!document.getElementById("levelMeter") || levelBusy) return;

  levelBusy = true;
  try {
    const response = await fetch("/api/audio-level", { cache: "no-store" });
    if (!response.ok) return;
    const payload = await response.json();
    if (currentStatus && payload.state !== currentStatus.state) {
      refresh();
      return;
    }
    updateAudioMeter(payload);
  } catch (error) {
    // The regular status poll handles connection errors; keep the meter quiet.
  } finally {
    levelBusy = false;
  }
}

async function showRecordList() {
  if (currentStatus?.state !== "idle" || recordsBusy) return;
  recordsBusy = true;
  deviceView = "records";
  selectedRecordId = null;
  selectedRecordPayload = null;
  qrMode = "wifi";
  renderKey = "";
  updateTopButton("idle");
  renderRecordsLoading("녹음 기록");
  try {
    const response = await fetch("/api/records", { cache: "no-store" });
    const items = await response.json();
    renderRecordList(Array.isArray(items) ? items : []);
  } catch (error) {
    showModal("연결 오류", "녹음 기록을 불러올 수 없습니다.");
    showHome();
  } finally {
    recordsBusy = false;
  }
}

async function showRecordDetail(sessionId) {
  if (currentStatus?.state !== "idle" || recordsBusy) return;
  recordsBusy = true;
  deviceView = "recordDetail";
  selectedRecordId = sessionId;
  selectedRecordPayload = null;
  qrMode = "wifi";
  updateTopButton("idle");
  renderRecordsLoading("QR 준비 중");
  try {
    const response = await fetch(`/api/records/${encodeURIComponent(sessionId)}`, { cache: "no-store" });
    const payload = await response.json();
    selectedRecordPayload = payload;
    renderRecordDetail(payload);
  } catch (error) {
    showModal("연결 오류", "선택한 녹음 정보를 불러올 수 없습니다.");
    recordsBusy = false;
    showRecordList();
  } finally {
    recordsBusy = false;
  }
}

function showHome() {
  deviceView = "home";
  selectedRecordId = null;
  selectedRecordPayload = null;
  qrMode = "wifi";
  renderKey = "";
  if (currentStatus) render(currentStatus);
}

function showModal(title, message, options = {}) {
  modalConfirm = options.onConfirm || null;
  modalTitle.textContent = title;
  modalMessage.textContent = message;
  modalClose.textContent = options.confirmText || "확인";
  modalClose.classList.toggle("danger", options.variant === "danger");
  modalCancel.textContent = options.cancelText || "아니오";
  modalCancel.classList.toggle("hidden", !modalConfirm);
  modalActions.classList.toggle("single", !modalConfirm);
  modal.classList.remove("hidden");
}

function closeModal() {
  modal.classList.add("hidden");
  modalConfirm = null;
  modalClose.classList.remove("danger");
  modalCancel.classList.add("hidden");
  modalActions.classList.add("single");
  modalClose.textContent = "확인";
}

screen.addEventListener("click", (event) => {
  const recordButton = event.target.closest("[data-record-id]");
  if (recordButton) {
    showRecordDetail(recordButton.dataset.recordId);
    return;
  }

  const button = event.target.closest("[data-action]");
  if (!button) return;
  if (button.dataset.action === "records") {
    showRecordList();
    return;
  }
  if (button.dataset.action === "home") {
    showHome();
    return;
  }
  if (button.dataset.action === "back-records") {
    showRecordList();
    return;
  }
  if (button.dataset.action === "qr-prev" || button.dataset.action === "qr-next") {
    qrMode = button.dataset.action === "qr-next" ? "link" : "wifi";
    renderKey = "";
    if (deviceView === "recordDetail" && selectedRecordPayload) {
      renderRecordDetail(selectedRecordPayload);
      updateTopButton("idle");
      return;
    }
    if (currentStatus) render(currentStatus);
    return;
  }
  if (button.dataset.action === "finish") {
    showModal("녹음 저장", "녹음을 저장하시겠습니까?\n저장하면 라즈베리파이에 보관되고 AI 처리가 시작됩니다.", {
      confirmText: "예",
      cancelText: "아니오",
      onConfirm: () => api("finish"),
    });
    return;
  }
  if (button.dataset.action === "cancel") {
    showModal("녹음 취소", "정말로 취소하시겠습니까?\n현재 녹음은 저장되지 않고 삭제됩니다.", {
      confirmText: "예",
      cancelText: "아니오",
      variant: "danger",
      onConfirm: () => api("cancel"),
    });
    return;
  }
  api(button.dataset.action);
});

homeButton.addEventListener("click", () => {
  if (deviceView === "recordDetail") {
    showRecordList();
    return;
  }
  if (deviceView !== "home") {
    showHome();
    return;
  }
  api("reset");
});
modalClose.addEventListener("click", () => {
  const confirm = modalConfirm;
  closeModal();
  if (confirm) confirm();
});
modalCancel.addEventListener("click", closeModal);

refresh();
setInterval(refresh, 1000);
setInterval(refreshAudioLevel, 80);

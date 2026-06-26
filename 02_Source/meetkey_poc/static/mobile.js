const app = document.getElementById("phoneApp");
const isListPage = location.pathname === "/records";
const sessionId = isListPage ? "" : decodeURIComponent(location.pathname.split("/").pop() || "");
let activePanel = "summary";
let currentPayload = null;
let copyResetTimer = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function inlineMarkdown(value) {
  let text = escapeHtml(value);
  const codeSpans = [];
  text = text.replace(/`([^`]+)`/g, (_, code) => {
    const token = `@@CODE_${codeSpans.length}@@`;
    codeSpans.push(`<code>${code}</code>`);
    return token;
  });
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  codeSpans.forEach((code, index) => {
    text = text.replace(`@@CODE_${index}@@`, code);
  });
  return text;
}

function isTableSeparator(line) {
  return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(line);
}

function isTableRow(line) {
  return line.includes("|") && line.split("|").length >= 3;
}

function parseTableRow(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map(cell => inlineMarkdown(cell.trim()));
}

function renderTable(lines, start) {
  const header = parseTableRow(lines[start]);
  let index = start + 2;
  const rows = [];
  while (index < lines.length && isTableRow(lines[index]) && !isTableSeparator(lines[index])) {
    rows.push(parseTableRow(lines[index]));
    index += 1;
  }

  return {
    html: `
      <div class="table-scroll">
        <table>
          <thead><tr>${header.map(cell => `<th>${cell}</th>`).join("")}</tr></thead>
          <tbody>
            ${rows.map(row => `<tr>${header.map((_, cellIndex) => `<td>${row[cellIndex] || ""}</td>`).join("")}</tr>`).join("")}
          </tbody>
        </table>
      </div>
    `,
    next: index,
  };
}

function renderListBlock(lines, start, ordered) {
  const tag = ordered ? "ol" : "ul";
  const pattern = ordered ? /^\s*\d+\.\s+(.+)$/ : /^\s*[-*]\s+(.+)$/;
  let index = start;
  const items = [];
  while (index < lines.length) {
    const match = lines[index].match(pattern);
    if (!match) break;
    items.push(`<li>${inlineMarkdown(match[1])}</li>`);
    index += 1;
  }
  return { html: `<${tag}>${items.join("")}</${tag}>`, next: index };
}

function renderParagraph(lines, start) {
  let index = start;
  const parts = [];
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) break;
    if (/^#{1,4}\s+/.test(line)) break;
    if (/^\s*[-*]\s+/.test(line)) break;
    if (/^\s*\d+\.\s+/.test(line)) break;
    if (isTableRow(line) && isTableSeparator(lines[index + 1] || "")) break;
    parts.push(line.trim());
    index += 1;
  }
  return { html: `<p>${inlineMarkdown(parts.join(" "))}</p>`, next: index };
}

function markdownToHtml(value) {
  const lines = String(value ?? "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = Math.min(4, heading[1].length + 1);
      blocks.push(`<h${level}>${inlineMarkdown(heading[2].trim())}</h${level}>`);
      index += 1;
      continue;
    }

    if (isTableRow(line) && isTableSeparator(lines[index + 1] || "")) {
      const table = renderTable(lines, index);
      blocks.push(table.html);
      index = table.next;
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const list = renderListBlock(lines, index, false);
      blocks.push(list.html);
      index = list.next;
      continue;
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const list = renderListBlock(lines, index, true);
      blocks.push(list.html);
      index = list.next;
      continue;
    }

    const paragraph = renderParagraph(lines, index);
    blocks.push(paragraph.html);
    index = paragraph.next;
  }

  return blocks.join("");
}

function compactLines(value) {
  return String(value ?? "").split(/\r?\n/).filter(line => line.trim()).length;
}

function compactText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function formatDate(value) {
  return String(value || "").replace("T", " ").slice(0, 16) || "날짜 확인 중";
}

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return `${seconds}초`;
  if (minutes < 60) return `${minutes}분`;
  return `${Math.floor(minutes / 60)}시간 ${minutes % 60}분`;
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

function dotClass(status) {
  if (status === "ready") return "ready";
  if (["error", "upload_failed", "missing"].includes(status)) return "error";
  return "";
}

function renderChrome(label = "MeetKey") {
  return `
    <div class="topline">
      <a class="brand" href="/records"><span class="brand-mark"></span><span>MeetKey</span></a>
      <span class="status-pill">${escapeHtml(label)}</span>
    </div>
  `;
}

function renderList(items) {
  app.innerHTML = `
    ${renderChrome(`${items.length}개 기록`)}
    <section class="hero">
      <div>
        <h1>녹음 기록</h1>
        <p>라즈베리파이에 저장된 회의록을 확인합니다.</p>
      </div>
    </section>
    ${items.length ? `
      <section class="list">
        ${items.map(item => `
          <a class="list-item" href="/record/${encodeURIComponent(item.session_id)}">
            <strong>${escapeHtml(item.title || "제목 생성 중")}</strong>
            <span>${escapeHtml(formatDate(item.created_at))} · ${escapeHtml(formatDuration(item.elapsed_seconds))} · ${escapeHtml(statusLabel(item.status))}</span>
          </a>
        `).join("")}
      </section>
    ` : `<section class="empty">아직 저장된 녹음이 없습니다.</section>`}
  `;
}

function renderDetail(payload) {
  currentPayload = payload;
  const record = payload.record || {};
  const chunks = Array.isArray(payload.chunks) ? payload.chunks : [];
  const readyChunks = chunks.filter(item => item.status === "ready").length;
  const failedChunks = chunks.filter(item => item.status === "error").length;
  const status = record.status || "missing";
  const ready = status === "ready";
  const hasSummary = Boolean(payload.summary);
  const hasTranscript = Boolean(payload.transcript);
  const downloads = [
    record.has_audio || record.audio_size ? ["audio", "원본 음성", "WAV"] : null,
    hasTranscript ? ["transcript", "전사문", "TXT"] : null,
    hasSummary ? ["summary", "요약문", "TXT"] : null,
  ].filter(Boolean);
  const panels = [
    hasSummary ? ["summary", "요약", `${compactLines(payload.summary)}줄`] : null,
    hasTranscript ? ["transcript", "전사문", `${compactLines(payload.transcript)}줄`] : null,
    downloads.length ? ["files", "파일", `${downloads.length}개`] : null,
  ].filter(Boolean);

  if (!panels.some(([key]) => key === activePanel)) {
    activePanel = panels[0]?.[0] || "files";
  }

  app.innerHTML = `
    ${renderChrome(statusLabel(status))}
    <section class="hero">
      <a class="back-link" href="/records">← 녹음 기록</a>
      <div>
        <h1>${escapeHtml(record.title || "제목 생성 중")}</h1>
        <p>${escapeHtml(record.status_label || "처리 상태를 확인하고 있습니다.")}</p>
      </div>
      <p class="muted">${escapeHtml(formatDate(record.created_at))} · ${escapeHtml(formatDuration(record.elapsed_seconds))}</p>
    </section>

    <section class="steps">
      ${step("녹음 저장", Boolean(record.has_audio || record.audio_size), record.audio_size ? `${record.audio_size} bytes` : "대기 중")}
      ${chunks.length ? step("구간 선처리", failedChunks ? false : readyChunks > 0, `${readyChunks}/${chunks.length} 완료`) : ""}
      ${step("Whisper 전사", ["summarizing", "ready"].includes(status), status === "transcribing" ? "진행 중" : "결과 대기")}
      ${step("gemma4 요약", ready, status === "summarizing" ? "진행 중" : "요약 대기")}
    </section>

    ${record.error ? `<section class="section"><h2>오류</h2><div class="markdown">${markdownToHtml(record.error)}</div></section>` : ""}
    ${chunks.length ? renderChunks(chunks) : ""}

    ${panels.length ? `
      <nav class="panel-tabs" aria-label="회의록 보기">
        ${panels.map(([key, label, meta]) => `
          <button class="panel-tab ${activePanel === key ? "active" : ""}" type="button" data-panel="${key}">
            <span>${escapeHtml(label)}</span>
            <small>${escapeHtml(meta)}</small>
          </button>
        `).join("")}
      </nav>
    ` : `<section class="empty">AI 처리 결과를 기다리고 있습니다.</section>`}

    <section class="content">
      ${hasSummary ? `
        <div class="section panel-view ${activePanel === "summary" ? "active" : ""}" data-view="summary">
          <div class="section-head">
            <div>
              <h2>회의 요약</h2>
              <p>${escapeHtml(compactText(payload.summary).slice(0, 42))}</p>
            </div>
            <div class="section-actions">
              <button class="small-download copy-button" type="button" data-copy="summary">
                <span>요약 복사</span>
                <small>복사</small>
              </button>
              ${downloadButton("summary", "요약 저장", "TXT")}
            </div>
          </div>
          <div class="markdown summary-markdown">${markdownToHtml(payload.summary)}</div>
        </div>
      ` : ""}
      ${hasTranscript ? `
        <div class="section panel-view ${activePanel === "transcript" ? "active" : ""}" data-view="transcript">
          <div class="section-head">
            <div>
              <h2>전사문</h2>
              <p>${escapeHtml(compactLines(payload.transcript))}줄</p>
            </div>
            ${downloadButton("transcript", "전사 저장", "TXT")}
          </div>
          <div class="markdown transcript-markdown">${markdownToHtml(payload.transcript)}</div>
        </div>
      ` : ""}
      ${downloads.length ? `
        <div class="section panel-view ${activePanel === "files" ? "active" : ""}" data-view="files">
          <h2>파일</h2>
          <div class="download-grid">
            ${downloads.map(([kind, label, format]) => `
              <a class="button download" href="${downloadUrl(kind)}" download>
                <span>${escapeHtml(label)}</span>
                <small>${escapeHtml(format)}</small>
              </a>
            `).join("")}
          </div>
        </div>
      ` : ""}
    </section>

    <div class="actions">
      <button class="button primary" ${ready ? "" : "disabled"} data-action="keep">${record.saved ? "보관됨" : "보관"}</button>
      <button class="button danger" data-action="delete">삭제</button>
    </div>
  `;
}

function renderChunks(chunks) {
  return `
    <section class="section chunk-section">
      <div class="section-head">
        <div>
          <h2>구간 처리</h2>
          <p>긴 회의는 앞부분부터 미리 처리됩니다.</p>
        </div>
      </div>
      <div class="chunk-list">
        ${chunks.map(item => `
          <div class="chunk-row">
            <span class="dot ${item.status === "ready" ? "ready" : item.status === "error" ? "error" : ""}"></span>
            <div>
              <strong>${escapeHtml(item.label || `${item.index}구간`)}</strong>
              <small>${escapeHtml(item.status_label || statusLabel(item.status))}</small>
            </div>
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function step(label, done, detail) {
  return `
    <div class="step">
      <span class="dot ${done ? "ready" : ""}"></span>
      <div><strong>${escapeHtml(label)}</strong><span>${escapeHtml(detail)}</span></div>
    </div>
  `;
}

function downloadButton(kind, label, format) {
  return `
    <a class="small-download" href="${downloadUrl(kind)}" download>
      <span>${escapeHtml(label)}</span>
      <small>${escapeHtml(format)}</small>
    </a>
  `;
}

function downloadUrl(kind) {
  return `/api/records/${encodeURIComponent(sessionId)}/download/${encodeURIComponent(kind)}`;
}

async function copyToClipboard(text) {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      // Fall through to the textarea fallback for local HTTP/mobile browsers.
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.top = "0";
  textarea.style.left = "0";
  textarea.style.width = "1px";
  textarea.style.height = "1px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);

  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch (_) {
    copied = false;
  }
  textarea.remove();
  return copied;
}

function setCopyButtonState(button, copied) {
  const label = button.querySelector("span");
  const meta = button.querySelector("small");
  if (!button.dataset.label) button.dataset.label = label?.textContent || "요약 복사";
  if (!button.dataset.meta) button.dataset.meta = meta?.textContent || "복사";

  button.classList.toggle("copied", copied);
  button.classList.toggle("failed", !copied);
  if (label) label.textContent = copied ? "복사됨" : "복사 실패";
  if (meta) meta.textContent = copied ? "완료" : "다시";

  clearTimeout(copyResetTimer);
  copyResetTimer = setTimeout(() => {
    button.classList.remove("copied", "failed");
    if (label) label.textContent = button.dataset.label;
    if (meta) meta.textContent = button.dataset.meta;
  }, 1600);
}

async function copySummary(button) {
  const summary = String(currentPayload?.summary || "").trim();
  if (!summary) {
    setCopyButtonState(button, false);
    return;
  }

  button.disabled = true;
  const copied = await copyToClipboard(summary);
  button.disabled = false;
  setCopyButtonState(button, copied);
}

async function loadList() {
  const response = await fetch("/api/records", { cache: "no-store" });
  const items = await response.json();
  renderList(Array.isArray(items) ? items : []);
}

async function loadDetail() {
  const response = await fetch(`/api/records/${encodeURIComponent(sessionId)}`, { cache: "no-store" });
  const payload = await response.json();
  renderDetail(payload);
  const status = payload.record?.status;
  if (!["ready", "error", "upload_failed", "missing"].includes(status)) {
    setTimeout(loadDetail, 1800);
  }
}

async function act(action) {
  if (action === "delete" && !confirm("이 녹음 기록을 삭제하시겠습니까?")) return;
  const response = await fetch(`/api/records/${encodeURIComponent(sessionId)}/${action}`, { method: "POST" });
  const payload = await response.json();
  if (action === "delete") {
    app.innerHTML = `
      ${renderChrome("삭제 완료")}
      <section class="hero">
        <h1>삭제 완료</h1>
        <p>이 회의 기록이 라즈베리파이에서 삭제되었습니다.</p>
        <a class="button primary wide" href="/records">녹음 기록으로 이동</a>
      </section>
    `;
    return;
  }
  renderDetail(payload);
}

app.addEventListener("click", (event) => {
  const copyButton = event.target.closest("[data-copy]");
  if (copyButton && !copyButton.disabled) {
    if (copyButton.dataset.copy === "summary") copySummary(copyButton);
    return;
  }

  const tab = event.target.closest("[data-panel]");
  if (tab) {
    activePanel = tab.dataset.panel;
    document.querySelectorAll("[data-panel]").forEach(item => {
      item.classList.toggle("active", item.dataset.panel === activePanel);
    });
    document.querySelectorAll("[data-view]").forEach(item => {
      item.classList.toggle("active", item.dataset.view === activePanel);
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }

  const button = event.target.closest("[data-action]");
  if (!button || button.disabled) return;
  act(button.dataset.action);
});

if (isListPage) loadList();
else loadDetail();

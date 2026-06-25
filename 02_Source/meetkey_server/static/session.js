const app = document.getElementById("app");
const sessionId = decodeURIComponent(location.pathname.split("/").pop() || "");
let activePanel = "summary";

function statusText(status) {
  const map = {
    uploaded: "업로드 완료",
    queued: "처리 대기 중",
    transcribing: "전사 진행 중",
    summarizing: "요약 진행 중",
    ready: "완료",
    error: "오류",
    missing: "대기 중",
  };
  return map[status] || status || "확인 중";
}

function dotClass(status) {
  if (status === "ready") return "ready";
  if (status === "error") return "error";
  return "";
}

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

function render(payload) {
  const session = payload.session || {};
  const status = session.status || "missing";
  const ready = status === "ready";
  const canDelete = status !== "missing";
  const hasSummary = Boolean(payload.summary);
  const hasTranscript = Boolean(payload.transcript);
  const downloads = [
    session.audio_size ? ["audio", "원본 음성", "WAV"] : null,
    hasTranscript ? ["transcript", "전사문", "TXT"] : null,
    hasSummary ? ["summary", "요약문", "TXT"] : null,
  ].filter(Boolean);
  const panels = [
    hasSummary ? ["summary", "요약", summaryMeta(payload.summary)] : null,
    hasTranscript ? ["transcript", "전사문", textMeta(payload.transcript)] : null,
    downloads.length ? ["files", "파일", `${downloads.length}개`] : null,
  ].filter(Boolean);
  if (!panels.some(([key]) => key === activePanel)) {
    activePanel = panels[0]?.[0] || "summary";
  }
  app.innerHTML = `
    <div class="topline">
      <div class="brand"><span class="brand-mark"></span><span>MeetKey</span></div>
      <span class="status-pill"><span class="dot ${dotClass(status)}"></span>${statusText(status)}</span>
    </div>

    <section class="hero">
      <div>
        <h1>회의록 처리</h1>
        <p>${escapeHtml(session.status_label || "라즈베리파이에서 녹음 파일을 기다리고 있습니다.")}</p>
      </div>
      <p class="muted">세션 ${escapeHtml(session.session_id || sessionId)}</p>
    </section>

    <section class="steps">
      ${step("녹음 업로드", ["uploaded", "queued", "transcribing", "summarizing", "ready", "error"].includes(status), session.audio_size ? `${session.audio_size} bytes` : "대기 중")}
      ${step("처리 순서", ["transcribing", "summarizing", "ready"].includes(status), status === "queued" ? "대기 중" : "준비 완료")}
      ${step("Whisper 전사", ["summarizing", "ready"].includes(status), status === "transcribing" ? "진행 중" : "전사 결과 생성")}
      ${step("gemma4 요약", ready, status === "summarizing" ? "진행 중" : "회의록 요약 생성")}
    </section>

    ${panels.length ? `
      <nav class="panel-tabs" aria-label="회의록 보기">
        ${panels.map(([key, label, meta]) => `
          <button class="panel-tab ${activePanel === key ? "active" : ""}" type="button" data-panel="${key}">
            <span>${escapeHtml(label)}</span>
            <small>${escapeHtml(meta)}</small>
          </button>
        `).join("")}
      </nav>
    ` : ""}

    <section class="content">
      ${session.error ? `<div class="section"><h2>오류</h2><div class="markdown">${markdownToHtml(session.error)}</div></div>` : ""}
      ${hasSummary ? `
        <div class="section panel-view ${activePanel === "summary" ? "active" : ""}" data-view="summary">
          <div class="section-head">
            <div>
              <h2>회의 요약</h2>
              <p>${escapeHtml(summaryMeta(payload.summary))}</p>
            </div>
            ${downloadButton("summary", "요약 저장", "TXT")}
          </div>
          <div class="markdown summary-markdown">${markdownToHtml(payload.summary)}</div>
        </div>
      ` : ""}
      ${hasTranscript ? `
        <div class="section panel-view ${activePanel === "transcript" ? "active" : ""}" data-view="transcript">
          <div class="section-head">
            <div>
              <h2>전사문</h2>
              <p>${escapeHtml(textMeta(payload.transcript))}</p>
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

    ${(ready || canDelete) ? `
      <div class="actions">
        <button class="button primary" ${ready ? "" : "disabled"} data-action="save">서버에 저장</button>
        <button class="button danger" data-action="delete">삭제</button>
      </div>
    ` : ""}
  `;
}

function summaryMeta(value) {
  const lines = compactLines(value);
  return lines ? `${lines}줄` : "요약 준비 중";
}

function textMeta(value) {
  const minutes = Math.max(1, Math.round(compactText(value).length / 850));
  return `${compactLines(value)}줄 · 약 ${minutes}분 분량`;
}

function compactText(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function compactLines(value) {
  return String(value ?? "").split(/\r?\n/).filter(line => line.trim()).length;
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
  return `/meetkey/api/sessions/${encodeURIComponent(sessionId)}/download/${encodeURIComponent(kind)}`;
}

function step(label, done, detail) {
  return `
    <div class="step">
      <span class="dot ${done ? "ready" : ""}"></span>
      <div><strong>${label}</strong><span>${escapeHtml(detail)}</span></div>
    </div>
  `;
}

async function load() {
  const response = await fetch(`/meetkey/api/sessions/${encodeURIComponent(sessionId)}`);
  const payload = await response.json();
  render(payload);
  const status = payload.session?.status;
  if (!["ready", "error", "missing"].includes(status)) {
    setTimeout(load, 1800);
  }
}

async function act(action) {
  const response = await fetch(`/meetkey/api/sessions/${encodeURIComponent(sessionId)}/${action}`, { method: "POST" });
  const payload = await response.json();
  if (action === "delete") {
    app.innerHTML = `
      <div class="topline"><div class="brand"><span class="brand-mark"></span><span>MeetKey</span></div></div>
      <section class="hero"><h1>삭제 완료</h1><p>이 회의 데이터가 서버에서 삭제되었습니다.</p></section>
    `;
    return;
  }
  render(payload);
}

app.addEventListener("click", (event) => {
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

load();

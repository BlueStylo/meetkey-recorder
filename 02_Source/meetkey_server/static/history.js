const app = document.getElementById("app");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function render(items) {
  app.innerHTML = `
    <div class="topline">
      <div class="brand"><span class="brand-mark"></span><span>MeetKey</span></div>
      <span class="status-pill"><span class="dot ready"></span>서버 기록</span>
    </div>

    <section class="hero">
      <div>
        <h1>녹음 기록</h1>
        <p>서버에 저장한 회의록을 휴대폰에서 다시 확인합니다.</p>
      </div>
      <p class="muted">${items.length}개 저장됨</p>
    </section>

    ${items.length ? `
      <section class="list">
        ${items.map(item => `
          <a class="list-item" href="/meetkey/session/${encodeURIComponent(item.session_id)}">
            <strong>${escapeHtml(titleFor(item))}</strong>
            <span class="muted">${escapeHtml(item.created_at || "")} · ${escapeHtml(item.status_label || item.status || "")}</span>
          </a>
        `).join("")}
      </section>
    ` : `<section class="empty">아직 서버에 저장된 회의록이 없습니다.</section>`}
  `;
}

function titleFor(item) {
  return `회의록 ${item.session_id || ""}`;
}

async function load() {
  const response = await fetch("/meetkey/api/history");
  const items = await response.json();
  render(items);
}

load();

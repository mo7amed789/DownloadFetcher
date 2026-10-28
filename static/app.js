const form = document.getElementById("search-form");
const queryInput = document.getElementById("query");
const resultsEl = document.getElementById("results");
const previewEl = document.getElementById("preview");
const rankingStatusEl = document.getElementById("ranking-status");
const chips = [...document.querySelectorAll(".chip")];

let currentFilter = "any";
let activeCard = null;
let pollTimer = null;
let activeSearchQuery = "";
let currentPage = 0;

chips.forEach((chip) => {
  chip.addEventListener("click", () => {
    chips.forEach((item) => item.classList.remove("active"));
    chip.classList.add("active");
    currentFilter = chip.dataset.filter;
  });
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = queryInput.value.trim();
  if (!query) return;
  activeSearchQuery = query;
  currentPage = 0;
  resultsEl.innerHTML = `<div class="empty-state">Searching the web for “${escapeHtml(query)}”…</div>`;
  previewEl.innerHTML = `<div class="empty-state">Pick a result to preview it.</div>`;
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, filter: currentFilter }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Search failed");
    renderResults(data.results, false);
    renderLoadMore(data.has_more);
    rankingStatusEl.hidden = false;
    rankingStatusEl.textContent = data.ai_ranking ? "AI-ranked results" : "Smart relevance-ranked results";
    rankingStatusEl.className = data.ai_ranking ? "ranking-status ai" : "ranking-status";
  } catch (error) {
    resultsEl.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  }
});

function renderResults(results, append = false) {
  if (!append) resultsEl.innerHTML = "";
  results.forEach((item) => {
    const card = document.createElement("article");
    card.className = item.thumb ? "card" : "card no-thumb";
    card.dir = "auto";
    card.innerHTML = `
      ${item.thumb ? `<img class="thumb" src="${escapeAttr(item.thumb)}" alt="">` : ""}
      <div>
        <h3>${escapeHtml(item.title)}</h3>
        <div class="url">${escapeHtml(item.url)}</div>
        <p>${escapeHtml(item.snippet || "")}</p>
        <div class="direct-links pending">Finding direct download links…</div>
      </div>
    `;
    card.addEventListener("click", () => {
      if (activeCard) activeCard.classList.remove("active");
      activeCard = card;
      card.classList.add("active");
      loadPreview(item.url, item.title);
    });
    resultsEl.appendChild(card);
    discoverDirectLinks(item.url, card, activeSearchQuery);
  });
}

function renderLoadMore(hasMore) {
  const old = document.getElementById("load-more-wrap");
  if (old) old.remove();
  if (!hasMore) return;
  const wrap = document.createElement("div");
  wrap.id = "load-more-wrap";
  wrap.className = "load-more-wrap";
  wrap.innerHTML = `<button class="load-more" id="load-more">Load more results</button>`;
  resultsEl.appendChild(wrap);
  document.getElementById("load-more").addEventListener("click", loadMoreResults);
}

async function loadMoreResults() {
  const button = document.getElementById("load-more");
  if (!button) return;
  button.disabled = true;
  button.textContent = "Loading more results…";
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: activeSearchQuery, filter: currentFilter, page: currentPage + 1 }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load more results");
    currentPage = data.page;
    const wrap = document.getElementById("load-more-wrap");
    if (wrap) wrap.remove();
    renderResults(data.results, true);
    renderLoadMore(data.has_more);
  } catch (error) {
    button.disabled = false;
    button.textContent = error.message;
  }
}

async function discoverDirectLinks(url, card, query) {
  const target = card.querySelector(".direct-links");
  try {
    const response = await fetch("/api/direct-links", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, query }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Direct-link lookup failed");
    if (!data.links || !data.links.length) {
      target.className = "direct-links empty";
      target.textContent = "No direct file link found on this page.";
      return;
    }
    target.className = "direct-links";
    target.innerHTML = `<strong>Relevant direct download link${data.links.length > 1 ? "s" : ""}</strong>` +
      data.links.map((link) => `
        <div class="direct-link-row">
          <a href="${escapeAttr(link.url)}" target="_blank" rel="noopener" title="Open direct file link">${escapeHtml(link.name || link.label)}</a>
          <span class="direct-meta">${escapeHtml(link.content_type || "file")} · ${escapeHtml(link.size || "size unknown")}</span>
          <button class="download-direct">Download</button>
          <button class="copy-link" data-url="${escapeAttr(link.url)}">Copy</button>
        </div>
      `).join("");
    target.querySelectorAll(".copy-link").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.stopPropagation();
        try {
          await navigator.clipboard.writeText(button.dataset.url);
          button.textContent = "Copied";
          setTimeout(() => { button.textContent = "Copy"; }, 1200);
        } catch {
          button.textContent = "Copy failed";
        }
      });
    });
    target.querySelectorAll(".download-direct").forEach((button, index) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        const link = data.links[index];
        startDirectDownload(link.url, link.name || link.label, button.closest(".direct-link-row"));
      });
    });
  } catch (error) {
    target.className = "direct-links empty";
    target.textContent = "Direct-link lookup unavailable for this page.";
  }
}

async function startDirectDownload(url, name, row) {
  let status = row.querySelector(".direct-download-status");
  if (!status) {
    status = document.createElement("span");
    status.className = "direct-download-status";
    row.appendChild(status);
  }
  status.className = "direct-download-status";
  status.textContent = "Starting…";
  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, name }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Download failed");
    const bar = document.createElement("div");
    bar.className = "progress mini";
    bar.innerHTML = "<span></span>";
    row.appendChild(bar);
    watchDownload(data.id, bar, bar.querySelector("span"), status);
  } catch (error) {
    status.className = "direct-download-status bad";
    status.textContent = error.message;
  }
}

async function loadPreview(url, title) {
  previewEl.innerHTML = `<div class="empty-state">Fetching content for preview…</div>`;
  try {
    const response = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Preview failed");
    renderPreview(data, title);
  } catch (error) {
    previewEl.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  }
}

function renderPreview(data, fallbackTitle) {
  const media = mediaMarkup(data);
  const sourcePage = data.kind === "html"
    ? `<div class="source-page-label">Real source page</div><iframe class="source-page" src="${escapeAttr(data.url)}" loading="lazy" referrerpolicy="no-referrer"></iframe>`
    : "";
  const files = (data.files || [])
    .map(
      (file) =>
        `<li data-url="${escapeAttr(file.url)}" data-name="${escapeAttr(file.name)}">
          <span>${escapeHtml(file.label)}</span>
          <span class="meta">${escapeHtml(file.name)}</span>
        </li>`
    )
    .join("");

  previewEl.innerHTML = `
    <h2>${escapeHtml(data.name || fallbackTitle || "Preview")}</h2>
    <div class="meta">${escapeHtml(data.content_type)} · ${escapeHtml(data.size_label || "size unknown")}<br>${escapeHtml(data.url)}</div>
    ${media}
    ${sourcePage}
    ${data.text ? `<pre class="preview-text">${escapeHtml(data.text)}</pre>` : ""}
    ${files ? `<h3>Direct files on this page</h3><ul class="file-list">${files}</ul>` : ""}
    <div class="actions">
      <button class="download-btn" id="download-now">Download this</button>
      <button class="ghost" id="open-source">Open full source page in browser</button>
    </div>
    <div class="progress" hidden><span></span></div>
    <div class="status" id="dl-status"></div>
  `;

  previewEl.querySelectorAll(".file-list li").forEach((row) => {
    row.addEventListener("click", () => loadPreview(row.dataset.url, row.dataset.name));
  });
  document.getElementById("download-now").addEventListener("click", () => startDownload(data.url, data.name));
  document.getElementById("open-source").addEventListener("click", () => window.open(data.url, "_blank"));
}

function mediaMarkup(data) {
  if (data.kind === "image") {
    return `<img class="preview-media" src="${escapeAttr(data.preview_url)}" alt="Preview">`;
  }
  if (data.kind === "video") {
    return `<video class="preview-media" controls src="${escapeAttr(data.preview_url)}"></video>`;
  }
  if (data.kind === "audio") {
    return `<audio class="preview-media" controls src="${escapeAttr(data.preview_url)}"></audio>`;
  }
  if (data.kind === "pdf") {
    return `<iframe class="preview-media" style="height:360px;width:100%;border:0;background:#fff" src="${escapeAttr(data.preview_url)}"></iframe>`;
  }
  return "";
}

async function startDownload(url, name) {
  const status = document.getElementById("dl-status");
  const bar = document.querySelector(".progress");
  const fill = bar.querySelector("span");
  bar.hidden = false;
  status.className = "status";
  status.textContent = "Starting download…";
  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, name }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Download failed");
    watchDownload(data.id, bar, fill, status);
  } catch (error) {
    status.className = "status bad";
    status.textContent = error.message;
  }
}

function watchDownload(id, bar, fill, status) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const response = await fetch(`/api/download/${id}`);
    const job = await response.json();
    if (job.total) {
      fill.style.width = `${Math.min(100, (job.received / job.total) * 100)}%`;
    } else {
      fill.style.width = job.status === "done" ? "100%" : "35%";
    }
    if (job.status === "downloading") {
      status.textContent = `Downloading ${job.name}… ${formatBytes(job.received)}${job.total ? " / " + formatBytes(job.total) : ""}`;
    }
    if (job.status === "done") {
      clearInterval(pollTimer);
      fill.style.width = "100%";
      status.className = "status ok";
      status.textContent = `Saved to ${job.path}`;
    }
    if (job.status === "error") {
      clearInterval(pollTimer);
      status.className = "status bad";
      status.textContent = job.error || "Download failed";
    }
  }, 400);
}

function formatBytes(value) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}

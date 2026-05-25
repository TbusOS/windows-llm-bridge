/* wlb — casts.html driver.
 *
 * Fetches /api/casts, populates the sidebar, plays the selected file
 * with asciinema-player. Vanilla JS — no build step, no framework.
 */

"use strict";

const listEl = document.getElementById("casts-list");
const playerHost = document.getElementById("player-host");
const metaEl = document.getElementById("casts-meta");
const refreshBtn = document.getElementById("refresh-btn");

let currentPlayer = null;
let currentPath = null;

function formatBytes(n) {
  if (n == null) return "?";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KiB";
  return (n / 1024 / 1024).toFixed(2) + " MiB";
}

function formatTime(epoch) {
  try {
    return new Date(epoch * 1000).toLocaleString();
  } catch (e) {
    return "?";
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function disposeCurrentPlayer() {
  if (currentPlayer) {
    try { currentPlayer.dispose(); } catch (e) { /* tolerate */ }
    currentPlayer = null;
  }
  playerHost.innerHTML = "";
}

async function loadList() {
  listEl.innerHTML = "loading…";
  try {
    const r = await fetch("/api/casts");
    if (!r.ok) throw new Error("HTTP " + r.status);
    const body = await r.json();
    if (!body.ok) throw new Error(body.error || "list failed");

    if (!body.casts || body.casts.length === 0) {
      listEl.innerHTML = `<div class="empty">
        No recordings yet.<br>
        Enable capture (<code>WLB_PTY_RECORD=1</code>) and open
        <a href="/pty.html">/pty.html</a> to start a session.
      </div>`;
      return;
    }

    listEl.innerHTML = "";
    for (const cast of body.casts) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "cast-item";
      item.dataset.path = cast.path;
      item.innerHTML = `
        <div class="cast-name">${escapeHtml(cast.filename)}</div>
        <div class="cast-meta-line">
          <span class="cast-host">${escapeHtml(cast.host)}</span>
          <span class="cast-size">${formatBytes(cast.size)}</span>
          <span class="cast-time">${escapeHtml(formatTime(cast.modified))}</span>
        </div>
      `;
      item.addEventListener("click", () => play(cast));
      if (cast.path === currentPath) item.classList.add("active");
      listEl.appendChild(item);
    }
  } catch (e) {
    listEl.innerHTML = `<div class="err">failed to list: ${escapeHtml(e.message)}</div>`;
  }
}

function play(cast) {
  if (typeof AsciinemaPlayer === "undefined") {
    metaEl.innerHTML = `<span class="err">asciinema-player.js didn't load — check network / CDN.</span>`;
    return;
  }
  disposeCurrentPlayer();
  currentPath = cast.path;
  document.querySelectorAll(".cast-item").forEach((b) => {
    b.classList.toggle("active", b.dataset.path === cast.path);
  });
  metaEl.innerHTML = `
    <code>${escapeHtml(cast.path)}</code>
    · ${formatBytes(cast.size)}
    · ${escapeHtml(formatTime(cast.modified))}
  `;
  const src = `/api/casts/${encodeURIComponent(cast.host)}/${encodeURIComponent(cast.filename)}`;
  try {
    currentPlayer = AsciinemaPlayer.create(src, playerHost, {
      autoPlay: true,
      fit: "width",
      theme: "asciinema",
      terminalFontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
      idleTimeLimit: 2,
    });
  } catch (e) {
    metaEl.innerHTML = `<span class="err">failed to create player: ${escapeHtml(e.message)}</span>`;
  }
}

refreshBtn.addEventListener("click", loadList);
loadList();

// wlb-api dashboard — vanilla JS, no framework, no build step.
//
// Talks to:
//   GET  /api/version            wlb version banner
//   GET  /api/describe           transports + capabilities matrix
//   GET  /api/status             active transport health
//   GET  /api/profile            merged active settings
//   GET  /api/tools              declared tool list
//   GET  /api/tools/<name>       full tool spec
//   WS   /ws/tool/<name>         live tool run

(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  async function getJson(url) {
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(`${url} → HTTP ${r.status}`);
    return r.json();
  }

  function statusTag(value) {
    const cls = { beta: "beta", stable: "ok", planned: "planned" }[value] || "";
    return `<span class="tag ${cls}">${value}</span>`;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ── version + profile banner ─────────────────────────────────
  async function loadBanner() {
    try {
      const v = await getJson("/api/version");
      $("#meta-version").textContent = `wlb v${v.wlb}`;
    } catch (e) {
      $("#meta-version").textContent = `version unknown`;
    }
    try {
      const p = await getJson("/api/profile");
      $("#meta-profile").textContent = `profile: ${p.profile_name}` +
        (p.profile_loaded ? "" : " (no file)");
    } catch (e) {
      $("#meta-profile").textContent = "profile: ?";
    }
  }

  // ── status card ──────────────────────────────────────────────
  async function loadStatus() {
    const card = $("#status-card");
    try {
      const r = await getJson("/api/status");
      const d = r.data || {};
      const h = d.health || {};
      const okTag = h.ok ? `<span class="tag ok">healthy</span>` : `<span class="tag warn">degraded</span>`;
      const items = Object.entries(h)
        .filter(([k]) => k !== "ok")
        .map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`)
        .join("");
      card.classList.remove("loading");
      card.innerHTML = `
        <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:10px;">
          <strong style="font-family:var(--mono);color:var(--accent);">${escapeHtml(d.transport || "?")}</strong>
          ${okTag}
        </div>
        <dl class="kv">${items}</dl>
      `;
    } catch (e) {
      card.classList.remove("loading");
      card.classList.add("error");
      card.textContent = `status unavailable: ${e.message}`;
    }
  }

  // ── profile card ─────────────────────────────────────────────
  async function loadProfile() {
    const card = $("#profile-card");
    try {
      const p = await getJson("/api/profile");
      const warn = p.warnings && p.warnings.length
        ? `<div class="tag warn" style="margin-top:6px">${p.warnings.length} warning(s)</div>`
        : "";
      card.classList.remove("loading");
      card.innerHTML = `
        <dl class="kv">
          <dt>name</dt><dd>${escapeHtml(p.profile_name)} ${p.profile_loaded ? "" : "<em>(no file on disk)</em>"}</dd>
          <dt>path</dt><dd><code>${escapeHtml(p.profile_path || "-")}</code></dd>
          <dt>transport</dt><dd>${escapeHtml(p.primary_transport)}</dd>
          <dt>ssh.host</dt><dd>${escapeHtml(p.ssh.host || "&lt;unset&gt;")}</dd>
          <dt>ssh.user</dt><dd>${escapeHtml(p.ssh.user || "&lt;unset&gt;")}</dd>
          <dt>http.url</dt><dd>${escapeHtml(p.http.url || "&lt;unset&gt;")}</dd>
        </dl>${warn}
      `;
    } catch (e) {
      card.classList.remove("loading");
      card.classList.add("error");
      card.textContent = `profile unavailable: ${e.message}`;
    }
  }

  // ── describe (transports + capabilities) ─────────────────────
  async function loadDescribe() {
    let r;
    try { r = await getJson("/api/describe"); }
    catch (e) { return; }
    const d = r.data || {};
    const tbody = $("#transports-table tbody");
    tbody.innerHTML = (d.transports || []).map((t) => `
      <tr>
        <td><code>${escapeHtml(t.name)}</code></td>
        <td>${statusTag(t.status)}</td>
        <td>${escapeHtml(t.description || "")}</td>
      </tr>
    `).join("");
    const cbody = $("#capabilities-table tbody");
    cbody.innerHTML = (d.capabilities || []).map((c) => `
      <tr>
        <td><code>${escapeHtml(c.name)}</code></td>
        <td>${statusTag(c.status)}</td>
        <td>${(c.supported_transports || []).map((x) => `<code>${escapeHtml(x)}</code>`).join(" ")}</td>
        <td>${escapeHtml(c.description || "")}</td>
      </tr>
    `).join("");
  }

  // ── tools list ───────────────────────────────────────────────
  let _tools = [];   // keep for run modal lookup

  async function loadTools() {
    const body = $("#tools-table tbody");
    body.innerHTML = "";
    $("#tools-warnings").textContent = "";
    let r;
    try { r = await getJson("/api/tools"); }
    catch (e) {
      body.innerHTML = `<tr><td colspan="6" style="color:var(--bad)">tools unavailable: ${escapeHtml(e.message)}</td></tr>`;
      return;
    }
    const d = r.data || {};
    _tools = d.tools || [];
    $("#tools-file").textContent = d.tools_file || "?";
    if (d.warnings && d.warnings.length) {
      $("#tools-warnings").textContent = "warnings: " + d.warnings.join(" / ");
    }
    if (_tools.length === 0) {
      body.innerHTML = `<tr><td colspan="6" style="color:var(--fg-dim);font-style:italic;">no tools declared — copy wlb-tools.example.toml as a starting point.</td></tr>`;
      return;
    }
    body.innerHTML = _tools.map((t) => `
      <tr>
        <td><code>${escapeHtml(t.name)}</code></td>
        <td>${escapeHtml(t.interpreter)}</td>
        <td>${(t.args || []).map((a) => `<code>${escapeHtml(a)}</code>`).join(", ") || "<em>—</em>"}</td>
        <td style="text-align:right;font-family:var(--mono);">${t.timeout}s</td>
        <td>${escapeHtml(t.description || "")}</td>
        <td><button class="run-btn" data-tool="${escapeHtml(t.name)}">Run</button></td>
      </tr>
    `).join("");
    $$(".run-btn").forEach((b) => {
      b.addEventListener("click", () => openRunModal(b.dataset.tool));
    });
  }

  // ── run modal ────────────────────────────────────────────────
  let _ws = null;

  function openRunModal(name) {
    const spec = _tools.find((t) => t.name === name);
    if (!spec) return;
    $("#modal-title").textContent = `Run ${name}`;
    const form = $("#args-form");
    form.innerHTML = "";
    (spec.args || []).forEach((a) => {
      const id = `arg-${a}`;
      form.innerHTML += `<label for="${id}">${escapeHtml(a)}</label>` +
        `<input type="text" id="${id}" name="${escapeHtml(a)}" autocomplete="off">`;
    });
    if (!(spec.args || []).length) {
      form.innerHTML = `<div class="help" style="grid-column:1/3;color:var(--fg-dim);font-style:italic;">no args declared.</div>`;
    } else {
      form.innerHTML += `<div class="help">values cannot contain newlines or shell metachars (<code>; &amp; | &lt; &gt; \` $</code>).</div>`;
    }
    $("#run-output").innerHTML = "";
    $("#run-output-pane").classList.add("hidden");
    $("#run-summary").classList.add("hidden");
    $("#run-progress-wrap").classList.add("hidden");
    $("#modal").classList.remove("hidden");
    setTimeout(() => {
      const first = form.querySelector("input");
      if (first) first.focus();
    }, 0);
  }

  function closeRunModal() {
    $("#modal").classList.add("hidden");
    if (_ws && _ws.readyState !== WebSocket.CLOSED) {
      try { _ws.close(); } catch (e) {}
    }
    _ws = null;
  }

  function appendOutputLine(text, cls) {
    const line = document.createElement("span");
    line.className = cls || "";
    line.textContent = text + "\n";
    const out = $("#run-output");
    out.appendChild(line);
    out.scrollTop = out.scrollHeight;
  }

  function runTool() {
    const name = $("#modal-title").textContent.replace(/^Run /, "");
    const args = {};
    $$("#args-form input").forEach((i) => { args[i.name] = i.value; });

    $("#modal-run").disabled = true;
    $("#run-output-pane").classList.remove("hidden");
    $("#run-status").textContent = "connecting…";
    $("#run-summary").classList.add("hidden");
    $("#run-output").innerHTML = "";

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/tool/${encodeURIComponent(name)}`;
    _ws = new WebSocket(url);

    _ws.addEventListener("open", () => {
      $("#run-status").textContent = "running…";
      _ws.send(JSON.stringify({ args }));
    });

    _ws.addEventListener("message", (msg) => {
      let ev;
      try { ev = JSON.parse(msg.data); }
      catch (e) {
        appendOutputLine(`[parse-error] ${msg.data}`, "match-bad");
        return;
      }
      if (ev.kind === "line") {
        appendOutputLine(ev.line || "", ev.stream === "stderr" ? "stderr" : "stdout");
      } else if (ev.kind === "progress") {
        $("#run-progress-wrap").classList.remove("hidden");
        $("#run-progress").value = ev.percent;
        $("#run-progress-label").textContent = `${ev.percent}%`;
      } else if (ev.kind === "match") {
        const cls = ev.pattern_label === "success" ? "match-ok" : "match-bad";
        appendOutputLine(`→ ${ev.pattern_label}: ${ev.match}`, cls);
      } else if (ev.kind === "done") {
        $("#run-status").textContent = ev.ok ? "succeeded" : "failed";
        const summary = $("#run-summary");
        summary.classList.remove("hidden");
        summary.classList.toggle("ok", !!ev.ok);
        summary.classList.toggle("bad", !ev.ok);
        if (ev.output) {
          summary.innerHTML =
            `<div>${ev.ok ? "✓" : "✗"} exit=${ev.output.exit_code} · ${ev.output.duration_ms}ms · via ${ev.output.via_transport}</div>` +
            (ev.output.log_path ? `<div>log: <code>${escapeHtml(ev.output.log_path)}</code></div>` : "") +
            (ev.output.failure_match ? `<div>failure: <code>${escapeHtml(ev.output.failure_match)}</code></div>` : "");
        } else {
          summary.innerHTML = `<div>✗ ${escapeHtml(ev.error_code || "FAILED")}${ev.line ? " — " + escapeHtml(ev.line) : ""}</div>`;
        }
        $("#modal-run").disabled = false;
      }
    });

    _ws.addEventListener("close", () => {
      if ($("#run-status").textContent === "running…" || $("#run-status").textContent === "connecting…") {
        $("#run-status").textContent = "connection closed";
        $("#modal-run").disabled = false;
      }
    });
    _ws.addEventListener("error", () => {
      $("#run-status").textContent = "websocket error";
      $("#modal-run").disabled = false;
    });
  }

  // ── wire up ──────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    loadBanner();
    loadStatus();
    loadProfile();
    loadDescribe();
    loadTools();

    $("#modal-close").addEventListener("click", closeRunModal);
    $("#modal-cancel").addEventListener("click", closeRunModal);
    $("#modal-run").addEventListener("click", runTool);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("#modal").classList.contains("hidden")) {
        closeRunModal();
      }
    });
  });
})();

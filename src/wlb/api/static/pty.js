// wlb PTY page — connects an xterm.js terminal to /ws/pty.
//
// Wire protocol (matches src/wlb/api/server.py):
//   First TEXT frame from client:
//     {"interpreter":"cmd"|"powershell"|"raw","cols":N,"rows":N}
//   Then:
//     client→server BINARY = keystrokes
//     client→server TEXT   = {"kind":"resize","cols":N,"rows":N}
//                          | {"kind":"close"}
//     server→client BINARY = raw PTY bytes
//     server→client TEXT   = {"kind":"exit","exit_code":N}
//                          | {"kind":"error","error":"..."}

(() => {
  "use strict";

  let term = null;
  let fit = null;
  let ws = null;
  let connected = false;

  function $(sel) { return document.querySelector(sel); }

  function setStatus(text, kind) {
    const el = $("#pty-status");
    el.textContent = text;
    el.className = "pty-status";
    if (kind) el.classList.add(kind);
  }

  function initTerm() {
    if (term) return;
    if (typeof Terminal === "undefined") {
      $("#pty-status").textContent = "xterm.js failed to load (network blocked? — see docs/pty.md)";
      $("#pty-status").classList.add("bad");
      return;
    }
    term = new Terminal({
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
      fontSize: 13,
      theme: {
        background: "#0f1115",
        foreground: "#e8eaee",
        cursor: "#7cc4ff",
      },
      cursorBlink: true,
      scrollback: 5000,
      convertEol: false,
    });
    if (typeof FitAddon !== "undefined") {
      fit = new FitAddon.FitAddon();
      term.loadAddon(fit);
    }
    term.open($("#terminal"));
    if (fit) fit.fit();
  }

  function connect() {
    initTerm();
    if (!term) return;
    if (connected) return;
    const interpreter = $("#interp-select").value;
    const dims = currentDims();
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/pty`;
    setStatus("connecting…");
    try {
      ws = new WebSocket(url);
    } catch (e) {
      setStatus(`failed to open WS: ${e.message}`, "bad");
      return;
    }
    ws.binaryType = "arraybuffer";

    ws.addEventListener("open", () => {
      connected = true;
      $("#connect-btn").disabled = true;
      $("#disconnect-btn").disabled = false;
      $("#interp-select").disabled = true;
      setStatus(`connected · ${interpreter} · ${dims.cols}×${dims.rows}`, "ok");
      ws.send(JSON.stringify({ interpreter, cols: dims.cols, rows: dims.rows }));
      term.focus();
    });

    ws.addEventListener("message", (msg) => {
      if (typeof msg.data === "string") {
        // Control event from server.
        try {
          const ev = JSON.parse(msg.data);
          if (ev.kind === "exit") {
            setStatus(`exited · code=${ev.exit_code}`, ev.exit_code === 0 ? "ok" : "warn");
            term.writeln(`\r\n\x1b[36m[wlb pty] session exited with code ${ev.exit_code}\x1b[0m`);
          } else if (ev.kind === "error") {
            setStatus(`error: ${ev.error}`, "bad");
            term.writeln(`\r\n\x1b[31m[wlb pty] error: ${ev.error}\x1b[0m`);
          }
        } catch {
          // ignore garbled control
        }
        return;
      }
      // Binary frame → write straight to xterm
      const data = msg.data instanceof ArrayBuffer
        ? new Uint8Array(msg.data)
        : msg.data;
      term.write(data);
    });

    ws.addEventListener("close", () => {
      connected = false;
      $("#connect-btn").disabled = false;
      $("#disconnect-btn").disabled = true;
      $("#interp-select").disabled = false;
      if ($("#pty-status").textContent.startsWith("connected"))
        setStatus("disconnected");
    });

    ws.addEventListener("error", () => {
      setStatus("websocket error", "bad");
    });

    // pipe keystrokes/paste to server
    term.onData((data) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        const enc = new TextEncoder().encode(data);
        ws.send(enc);
      }
    });
  }

  function disconnect() {
    if (!ws) return;
    try { ws.send(JSON.stringify({ kind: "close" })); } catch (e) {}
    try { ws.close(); } catch (e) {}
  }

  function currentDims() {
    if (fit && term) {
      const d = fit.proposeDimensions();
      if (d && d.cols && d.rows) return { cols: d.cols, rows: d.rows };
    }
    if (term) return { cols: term.cols, rows: term.rows };
    return { cols: 80, rows: 24 };
  }

  function onResize() {
    if (!term) return;
    if (fit) {
      try { fit.fit(); } catch (e) {}
    }
    const d = currentDims();
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ kind: "resize", cols: d.cols, rows: d.rows }));
    }
    if (connected) setStatus(`connected · ${$("#interp-select").value} · ${d.cols}×${d.rows}`, "ok");
  }

  document.addEventListener("DOMContentLoaded", () => {
    // Wait for xterm.js to load (it has `defer`).
    let tries = 0;
    const ready = setInterval(() => {
      if (typeof Terminal !== "undefined" || tries++ > 50) {
        clearInterval(ready);
        initTerm();
      }
    }, 100);

    $("#connect-btn").addEventListener("click", connect);
    $("#disconnect-btn").addEventListener("click", disconnect);
    window.addEventListener("resize", onResize);
  });
})();

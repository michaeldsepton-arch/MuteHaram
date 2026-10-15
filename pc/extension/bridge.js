// Runs in every frame of the page (extension side). Relays the live-mode connection between
// inpage.js (inside the page) and the background script, which owns the real WebSocket.
if (!window.__mhBridge) {
  window.__mhBridge = true;
  const ports = {};
  window.addEventListener("message", (e) => {
    const d = e.data;
    if (e.source !== window || !d || d.mhBridge !== "toExt") return;
    const reply = (m) => window.postMessage(Object.assign({ mhBridge: "toPage", id: d.id }, m), "*");
    if (d.t === "open") {
      const p = chrome.runtime.connect({ name: "mhlive" });
      ports[d.id] = p;
      p.onMessage.addListener((m) => reply(m));
      p.onDisconnect.addListener(() => { delete ports[d.id]; reply({ t: "close", code: 4101, reason: "extension disconnected" }); });
      p.postMessage({ t: "open", port: d.port });
    } else if (ports[d.id]) {
      try { ports[d.id].postMessage(d); if (d.t === "close") { ports[d.id].disconnect(); delete ports[d.id]; } } catch (err) {}
    }
  });
}

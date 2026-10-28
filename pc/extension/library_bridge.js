// Runs in the MuteHaram library window (served by the helper on this PC). Lets it ask the extension
// for a fresh video link when a website's link has expired (the extension opens the episode's player).
document.documentElement.dataset.mhExt = "1";
window.addEventListener("message", (e) => {
  const d = e.data;
  if (e.source !== window || !d || d.mhLib !== "relink") return;
  chrome.runtime.sendMessage({ t: "relink", id: d.id, player: d.player }, (res) => {
    const err = chrome.runtime.lastError;
    window.postMessage({ mhLib: "relinked", key: d.key, res: res || { ok: false, error: err ? err.message : "no answer" } }, "*");
  });
});

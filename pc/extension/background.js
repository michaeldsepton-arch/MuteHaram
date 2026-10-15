// MuteHaram browser extension.
//  * Remembers video stream addresses (.m3u8 / .mpd / .mp4 ...) each tab loads.
//  * On click, sends the current video to the local MuteHaram helper, which opens
//    it in the player with the music removed, then pauses the page's own video.
// Needs the helper running (start_helper.bat).

const HELPER = "http://127.0.0.1:47800";
const MEDIA_RE = /\.(m3u8|mpd|mp4|m4v|webm|mkv|mov)(\?|#|$)/i;
const SKIP_RE = /(\.ts(\?|$)|\.m4s(\?|$)|\.aac(\?|$)|\.vtt|\.srt|\/seg[-_]?\d|thumb|sprite|storyboard|\.mp4\.(jpg|png))/i;
const KEEP_HEADERS = ["referer", "user-agent", "cookie", "origin", "authorization"];
const YT_RE = /^https:\/\/((www|m|music)\.)?youtube\.com\/(watch|shorts|live)|^https:\/\/youtu\.be\//;

const found = {}; // tabId -> [{url, headers, t}]

chrome.webRequest.onSendHeaders.addListener(
  (d) => {
    if (d.tabId < 0 || !MEDIA_RE.test(d.url) || SKIP_RE.test(d.url)) return;
    const headers = {};
    for (const h of d.requestHeaders || []) {
      if (KEEP_HEADERS.includes(h.name.toLowerCase())) headers[h.name] = h.value;
    }
    const list = (found[d.tabId] ||= []);
    if (!list.some((x) => x.url === d.url)) list.push({ url: d.url, headers, t: Date.now() });
    if (list.length > 40) list.shift();
  },
  { urls: ["<all_urls>"] },
  ["requestHeaders", "extraHeaders"]
);

chrome.tabs.onRemoved.addListener((id) => delete found[id]);
chrome.tabs.onUpdated.addListener((id, info) => {
  if (info.status === "loading" && info.url) found[id] = []; // new page in this tab
});

function pickCandidate(list) {
  if (!list || !list.length) return null;
  const rank = (u) => (/\.m3u8/i.test(u) ? 3 : /\.mpd/i.test(u) ? 2 : 1);
  const isMaster = (u) => !/(chunk|segment|media_|index_|-v\d|_\d+p)/i.test(u);
  return [...list].sort((a, b) =>
    rank(b.url) - rank(a.url) || isMaster(b.url) - isMaster(a.url) || b.t - a.t)[0];
}

async function pagePosition(tabId) {
  try {
    const res = await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      func: () => {
        const v = [...document.querySelectorAll("video")]
          .sort((a, b) => (b.duration || 0) - (a.duration || 0))[0];
        return v ? { time: v.currentTime, dur: v.duration || 0 } : null;
      },
    });
    const best = res.map((r) => r.result).filter(Boolean).sort((a, b) => b.dur - a.dur)[0];
    return best ? best.time : 0;
  } catch (e) {
    return 0;
  }
}

function pausePage(tabId) {
  chrome.scripting.executeScript({
    target: { tabId, allFrames: true },
    func: () => document.querySelectorAll("video").forEach((v) => v.pause()),
  }).catch(() => {});
}

function badge(tabId, text, color, title) {
  chrome.action.setBadgeText({ tabId, text });
  chrome.action.setBadgeBackgroundColor({ tabId, color });
  if (title) chrome.action.setTitle({ tabId, title });
  setTimeout(() => chrome.action.setBadgeText({ tabId, text: "" }), 6000);
}

chrome.action.onClicked.addListener(async (tab) => {
  const start = await pagePosition(tab.id);
  let payload;
  if (YT_RE.test(tab.url || "")) {
    payload = { url: tab.url, start };
  } else {
    const c = pickCandidate(found[tab.id]);
    if (!c) {
      return badge(tab.id, "?", "#b26a00",
        "No video stream seen on this page yet. Press play on the video, wait a few seconds, then click again.");
    }
    payload = { url: c.url, headers: c.headers, referer: c.headers.Referer || tab.url, start, page: tab.url, title: tab.title };
  }
  try {
    const r = await fetch(HELPER + "/play", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-MuteHaram": "1" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error("helper said " + r.status);
    pausePage(tab.id);
    badge(tab.id, "OK", "#1a7f37", "Opening in the MuteHaram player ...");
  } catch (e) {
    badge(tab.id, "ERR", "#c62828",
      "Can't reach the MuteHaram helper. Start it with start_helper.bat. (" + e.message + ")");
  }
});

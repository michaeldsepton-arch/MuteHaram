// MuteHaram browser extension.
//  * Remembers video stream addresses (.m3u8 / .mpd / .mp4 ... or anything the server calls a
//    playlist/video) that each tab loads, plus caption files.
//  * On click, sends the current video to the local MuteHaram helper, which opens it in the
//    player with the music removed, then pauses the page's own video.
// Needs the helper running (start_helper.bat).

const HELPER = "http://127.0.0.1:47800";
const MEDIA_RE = /\.(m3u8|mpd|mp4|m4v|webm|mkv|mov)(\?|#|$)/i;
const SUB_RE = /\.(vtt|srt|ass|ssa)(\?|#|$)/i;
const SKIP_RE = /(\.ts(\?|$)|\.m4s(\?|$)|\.aac(\?|$)|\.vtt|\.srt|\/seg[-_]?\d|thumb|sprite|storyboard|\.mp4\.(jpg|png))/i;
// What the SERVER says a response is (catches playlists with odd file names):
const PLAYLIST_TYPE_RE = /(mpegurl|dash\+xml|vnd\.apple)/i;
const VIDEO_TYPE_RE = /^(video\/(mp4|webm|x-matroska)|audio\/(mp4|webm))/i;
const KEEP_HEADERS = ["referer", "user-agent", "cookie", "origin", "authorization"];
const YT_RE = /^https:\/\/((www|m|music)\.)?youtube\.com\/(watch|shorts|live)|^https:\/\/youtu\.be\//;

const found = {};     // tabId -> [{url, headers, t, how}]
const orphans = [];   // same, for requests Chrome could not attribute to a tab (web workers!)
const subsFound = {}; // tabId -> [caption file urls]
const recent = {};    // tabId -> last requests (for the "what did this page load" report)
const reqHeaders = new Map(); // requestId -> kept headers (so the content-type check can reuse them)

function add(list, c) {
  if (!list.some((x) => x.url === c.url)) list.push(c);
  if (list.length > 40) list.shift();
}

function noteRecent(tabId, d) {
  if (["image", "stylesheet", "font", "script", "ping", "csp_report"].includes(d.type)) return;
  const l = (recent[tabId] ||= []);
  l.push(`${d.type} ${d.method} ${d.url.slice(0, 230)}`);
  if (l.length > 70) l.shift();
}

function onSendHeaders(d) {
  const headers = {};
  for (const h of d.requestHeaders || []) {
    if (KEEP_HEADERS.includes(h.name.toLowerCase())) headers[h.name] = h.value;
  }
  reqHeaders.set(d.requestId, headers);
  if (reqHeaders.size > 400) reqHeaders.delete(reqHeaders.keys().next().value);
  noteRecent(d.tabId, d);

  if (SUB_RE.test(d.url)) {
    if (d.tabId >= 0) {
      const l = (subsFound[d.tabId] ||= []);
      if (!l.includes(d.url)) l.push(d.url);
      if (l.length > 12) l.shift();
    }
    return;
  }
  if (!MEDIA_RE.test(d.url) || SKIP_RE.test(d.url)) return;
  const c = { url: d.url, headers, t: Date.now(), how: "extension" };
  if (d.tabId >= 0) add((found[d.tabId] ||= []), c);
  else add(orphans, { ...c, initiator: d.initiator || "" });   // from a web worker: no tab known
}

function onHeadersReceived(d) {
  if (d.statusCode >= 400 || SKIP_RE.test(d.url) || MEDIA_RE.test(d.url)) return;
  let ct = "";
  for (const h of d.responseHeaders || []) if (h.name.toLowerCase() === "content-type") ct = h.value || "";
  if (!(PLAYLIST_TYPE_RE.test(ct) || VIDEO_TYPE_RE.test(ct))) return;
  const c = { url: d.url, headers: reqHeaders.get(d.requestId) || {}, t: Date.now(), how: "content-type " + ct };
  if (d.tabId >= 0) add((found[d.tabId] ||= []), c);
  else add(orphans, { ...c, initiator: d.initiator || "" });
}

function listen(event, fn, extra) {
  try {
    event.addListener(fn, { urls: ["<all_urls>"] }, extra);
  } catch (e) {
    // Firefox does not know "extraHeaders"
    event.addListener(fn, { urls: ["<all_urls>"] }, extra.filter((x) => x !== "extraHeaders"));
  }
}
listen(chrome.webRequest.onSendHeaders, onSendHeaders, ["requestHeaders", "extraHeaders"]);
listen(chrome.webRequest.onHeadersReceived, onHeadersReceived, ["responseHeaders", "extraHeaders"]);

chrome.tabs.onRemoved.addListener((id) => { delete found[id]; delete subsFound[id]; delete recent[id]; });
chrome.tabs.onUpdated.addListener((id, info) => {
  if (info.status === "loading" && info.url) { found[id] = []; subsFound[id] = []; recent[id] = []; } // new page
});

function pickCandidate(tabId) {
  let list = found[tabId] || [];
  if (!list.length) {
    // nothing attributed to this tab: use what web workers fetched in the last 3 minutes
    list = orphans.filter((c) => Date.now() - c.t < 180000);
  }
  if (!list.length) return null;
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
  setTimeout(() => chrome.action.setBadgeText({ tabId, text: "" }), 8000);
}

async function post(path, body) {
  return fetch(HELPER + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-MuteHaram": "1" },
    body: JSON.stringify(body),
  });
}

// Right-click the toolbar icon -> "small window" opens the player as a corner picture-in-picture.
try {
  chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.create({
      id: "muteharam-pip", title: "Open in a small always-on-top window", contexts: ["action"],
    });
  });
  chrome.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId === "muteharam-pip" && tab) openInPlayer(tab, true);
  });
} catch (e) { /* contextMenus not available: normal click still works */ }

chrome.action.onClicked.addListener((tab) => openInPlayer(tab, false));

async function openInPlayer(tab, pip) {
  const start = await pagePosition(tab.id);
  let payload;
  if (YT_RE.test(tab.url || "")) {
    payload = { url: tab.url, start, pip, subs: subsFound[tab.id] || [] };
  } else {
    const c = pickCandidate(tab.id);
    if (c) {
      payload = { url: c.url, headers: c.headers, referer: c.headers.Referer || tab.url, start, page: tab.url,
                  title: tab.title, pip, subs: subsFound[tab.id] || [] };
    } else {
      // No stream address seen. Save a report of what the page loaded (so it can be examined)
      // and still try the page address itself: yt-dlp can find the video on some sites.
      try {
        await post("/debug", { page: tab.url, requests: recent[tab.id] || [], orphans: orphans.slice(-20).map((o) => o.url) });
      } catch (e) { /* helper not running: handled below */ }
      payload = { url: tab.url, page: tab.url, start, pip, subs: subsFound[tab.id] || [] };
      badge(tab.id, "?", "#b26a00",
        "No video stream seen on this page. Make sure the video is playing, then click again. " +
        "A report of what the page loaded was saved in the pc/logs folder (extension-debug-*.txt).");
    }
  }
  try {
    const r = await post("/play", payload);
    if (!r.ok) throw new Error("helper said " + r.status);
    pausePage(tab.id);
    if (!(await chrome.action.getBadgeText({ tabId: tab.id }))) badge(tab.id, "OK", "#1a7f37", "Opening in the MuteHaram player ...");
  } catch (e) {
    badge(tab.id, "ERR", "#c62828",
      "Can't reach the MuteHaram helper. Start it with start_helper.bat. (" + e.message + ")");
  }
}

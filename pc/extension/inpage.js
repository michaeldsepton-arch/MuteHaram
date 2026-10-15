// MuteHaram live mode: remove music INSIDE the page, while the video plays.
//
// Runs in the page that holds the <video> (injected by the extension, or loaded by a test page).
//  1. The video's sound is routed through the Web Audio API and streamed to the MuteHaram live
//     server on this computer (ws://127.0.0.1:<port>), which returns cleaned blocks.
//  2. The cleaned sound is played back a FIXED time (L) later than the original; whenever a block is
//     missing, the original (equally delayed) sound plays instead, so the picture never loses sync.
//  3. The picture is held back by the same L: frames are copied into a queue and drawn on a canvas
//     laid exactly over the video, L later. The page's own controls keep working (they sit above).
//
// Configuration comes from window.__mhCfg = { port, token, windowMs, marginMs, budgetMs, debug }.
// Running the script a second time turns the feature off again.
(() => {
  "use strict";
  if (window.__mhLive) { window.__mhLive.toggle(); return; }
  const cfg = Object.assign({ port: 47802, token: "", windowMs: 3000, marginMs: 500, budgetMs: 900,
                              maxWidth: 1280, debug: false }, window.__mhCfg || {});
  const RATE = 44100;

  // ---------------------------------------------------------------- find the video
  function pickVideo() {
    const vids = [...document.querySelectorAll("video")]
      .filter((v) => v.clientWidth >= 200 && v.clientHeight >= 110)
      .sort((a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight);
    return vids[0] || null;
  }
  const video = pickVideo();
  if (!video) return;                       // another frame of the page probably holds the video

  const log = (...a) => console.log("[MuteHaram live]", ...a);
  const dbg = { beeps: [], flashes: [], blocks: 0, late: 0, events: [] };
  if (cfg.debug) window.__mhDebug = dbg;

  // Cross-origin media without CORS would be silenced for good by createMediaElementSource.
  const src0 = video.currentSrc || video.src || "";
  if (src0 && !src0.startsWith("blob:") && !src0.startsWith("data:") && !video.srcObject) {
    try {
      const u = new URL(src0, location.href);
      if (u.origin !== location.origin && !video.crossOrigin) {
        console.warn("[MuteHaram live] this video comes from another site without permission to read its sound; not touching it");
        return;
      }
    } catch (e) { /* fall through */ }
  }

  // ---------------------------------------------------------------- status badge
  const badge = document.createElement("div");
  badge.style.cssText = "position:fixed;z-index:2147483646;left:8px;top:8px;padding:3px 8px;font:12px/1.3 system-ui,sans-serif;" +
    "color:#fff;background:rgba(0,0,0,.65);border-radius:4px;pointer-events:none;opacity:1;transition:opacity .6s";
  let badgeTimer = null;
  function say(text, keep) {
    badge.textContent = "MuteHaram: " + text;
    badge.style.opacity = "1";
    clearTimeout(badgeTimer);
    if (!keep) badgeTimer = setTimeout(() => (badge.style.opacity = "0"), 4000);
    if (!badge.isConnected) (video.parentElement || document.body).appendChild(badge);
  }

  // ---------------------------------------------------------------- audio worklet code (runs on the audio thread)
  function workletMain() {
    class MHCore extends AudioWorkletProcessor {
      constructor(options) {
        super();
        const p = options.processorOptions;
        this.L = p.latencyFrames; this.H = p.hop; this.cap = p.capFrames; this.gen = p.gen;
        this.orig = new Float32Array(this.cap * 2);
        this.clean = new Float32Array(this.cap * 2);
        this.ok = new Float64Array(2048);          // ok[k % 2048] = k + 1 when block k is present
        this.n = 0;                                // frames processed since (re)start
        this.sendN = 0; this.send = new Float32Array(2048 * 2);
        this.armed = true; this.beepOn = false; this.debug = !!p.debug;
        this.port.onmessage = (e) => {
          const m = e.data;
          if (m.t === "block") {
            if (m.gen !== this.gen) return;
            const d = m.data, k = m.k, H = this.H;
            for (let j = 0; j < m.n; j++) {
              const slot = (k * H + j) % this.cap;
              this.clean[2 * slot] = d[2 * j] / 32768; this.clean[2 * slot + 1] = d[2 * j + 1] / 32768;
            }
            this.ok[k % 2048] = k + 1;
          } else if (m.t === "reset") {
            this.gen = m.gen; this.n = 0; this.sendN = 0; this.ok.fill(0);
          } else if (m.t === "latency") {
            this.L = m.latencyFrames;
          }
        };
      }
      process(inputs, outputs) {
        const inp = inputs[0], out = outputs[0];
        const n = out[0].length;
        const l = inp.length ? inp[0] : null, r = inp.length > 1 ? inp[1] : l;
        const cap = this.cap, H = this.H, L = this.L;
        let peakOut = 0;
        for (let i = 0; i < n; i++) {
          const slot = this.n % cap;
          const a = l ? l[i] : 0, b = r ? r[i] : 0;
          this.orig[2 * slot] = a; this.orig[2 * slot + 1] = b;
          this.send[2 * this.sendN] = a; this.send[2 * this.sendN + 1] = b; this.sendN++;
          const idx = this.n - L;
          let ol = 0, or = 0;
          if (idx >= 0) {
            const s2 = idx % cap, k = Math.floor(idx / H);
            if (this.ok[k % 2048] === k + 1) { ol = this.clean[2 * s2]; or = this.clean[2 * s2 + 1]; }
            else { ol = this.orig[2 * s2]; or = this.orig[2 * s2 + 1]; this.lateFrames = (this.lateFrames || 0) + 1; }
          }
          out[0][i] = ol; if (out.length > 1) out[1][i] = or;
          if (this.debug) { const v = Math.abs(ol); if (v > peakOut) peakOut = v; }
          this.n++;
        }
        if (this.debug) {          // report the moment a loud tone starts in the OUTPUT (for sync measurements)
          if (this.armed && peakOut > 0.25) { this.armed = false; this.port.postMessage({ t: "beep", time: currentTime, gen: this.gen }); }
          else if (!this.armed && peakOut < 0.1) { this.silentQ = (this.silentQ || 0) + 1; if (this.silentQ > 40) { this.armed = true; this.silentQ = 0; } }
          else this.silentQ = 0;
        }
        if (this.sendN >= 2048) {
          const buf = this.send.slice(0, this.sendN * 2);
          this.port.postMessage({ t: "in", gen: this.gen, buf }, [buf.buffer]);
          this.sendN = 0;
          this.port.postMessage({ t: "late", frames: this.lateFrames || 0 });
        }
        return true;
      }
    }
    registerProcessor("mh-core", MHCore);
  }

  // ---------------------------------------------------------------- state
  let ctx = null, srcNode = null, node = null, ws = null, enabled = true, gen = 1;
  let hop = 0, margin = 0, latencyFrames = 0, delayMs = 0;
  let videoOffsetMs = Number(localStorage.getItem("mh_video_offset_ms") || 0);
  let closed = false, retryTimer = null;

  // ---------------------------------------------------------------- picture delay (canvas over the video)
  const canvas = document.createElement("canvas");
  canvas.style.cssText = "position:fixed;pointer-events:none;background:#000";
  const g2d = canvas.getContext("2d");
  const queue = [];                 // {bm, due}
  let origOpacity = video.style.opacity, lastDrawn = null, rafId = 0, rvfcOn = true;

  function layout() {
    const r = video.getBoundingClientRect();
    canvas.style.left = r.left + "px"; canvas.style.top = r.top + "px";
    canvas.style.width = r.width + "px"; canvas.style.height = r.height + "px";
    const w = Math.max(2, Math.round(r.width * (window.devicePixelRatio || 1)));
    const h = Math.max(2, Math.round(r.height * (window.devicePixelRatio || 1)));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; lastDrawn = null; }
    const cs = getComputedStyle(video);
    canvas.style.objectFit = cs.objectFit;
  }

  function drawFrame(bm) {
    // letterbox like object-fit: contain
    const cw = canvas.width, ch = canvas.height;
    const s = Math.min(cw / bm.width, ch / bm.height);
    const w = bm.width * s, h = bm.height * s;
    g2d.fillStyle = "#000"; g2d.fillRect(0, 0, cw, ch);
    g2d.drawImage(bm, (cw - w) / 2, (ch - h) / 2, w, h);
  }

  function tick(now) {
    if (now === undefined) now = performance.now();
    if (tick.fromRaf) { lastRaf = performance.now(); }
    layout();
    let show = null;
    while (queue.length && queue[0].due <= now) {
      if (show) show.bm.close();
      show = queue.shift();
    }
    if (show) {
      if (lastDrawn) lastDrawn.close();
      lastDrawn = show.bm;
      drawFrame(lastDrawn);
      if (cfg.debug) {            // sample the middle pixel: a white flash in the picture
        const px = g2d.getImageData(canvas.width >> 1, canvas.height >> 1, 1, 1).data;
        const bright = px[0] > 200 && px[1] > 200 && px[2] > 200;
        if (bright && !tick.prev) dbg.flashes.push(performance.now());
        tick.prev = bright;
      }
    } else if (lastDrawn && canvas.width && !tick.keep) {
      // nothing new: keep showing the last frame (redraw after a resize)
      drawFrame(lastDrawn);
    }
    while (queue.length > 600) queue.shift().bm.close();
  }

  let lastRvfc = 0, lastRaf = 0, lastCaptureTime = -1;
  function captureFrame() {
    const vw = video.videoWidth, vh = video.videoHeight;
    if (vw && vh && enabled) {
      const sc = Math.min(1, cfg.maxWidth / vw);
      const due = performance.now() + delayMs + videoOffsetMs;
      createImageBitmap(video, { resizeWidth: Math.round(vw * sc), resizeHeight: Math.round(vh * sc), resizeQuality: "low" })
        .then((bm) => { if (enabled) queue.push({ bm, due }); else bm.close(); })
        .catch(() => {});
    }
  }
  function onVideoFrame() {
    if (!rvfcOn) return;
    lastRvfc = performance.now();
    lastCaptureTime = video.currentTime;
    captureFrame();
    video.requestVideoFrameCallback(onVideoFrame);
  }
  // Background tabs / hidden pages stop frame callbacks: a timer takes over so the picture keeps flowing.
  setInterval(() => {
    if (!rvfcOn || !node) return;
    const now = performance.now();
    if (now - lastRvfc > 120 && !video.paused && video.currentTime !== lastCaptureTime) {
      lastCaptureTime = video.currentTime;
      captureFrame();
    }
    if (now - lastRaf > 120) tick(now);
  }, 33);

  function startPicture() {
    video.parentElement.insertBefore(canvas, video.nextSibling);
    // The video stays visible UNDER the canvas: browsers stop reporting frames for an invisible video.
    layout();
    const loop = (t) => { rafId = requestAnimationFrame(loop); tick.fromRaf = true; tick(t); tick.fromRaf = false; };
    rafId = requestAnimationFrame(loop);
    video.requestVideoFrameCallback(onVideoFrame);
  }
  function stopPicture() {
    rvfcOn = false;
    cancelAnimationFrame(rafId);
    canvas.remove();
    while (queue.length) queue.shift().bm.close();
    if (lastDrawn) { lastDrawn.close(); lastDrawn = null; }
  }

  // ---------------------------------------------------------------- audio chain + server connection
  async function setupAudio() {
    const AC = window.AudioContext || window.webkitAudioContext;
    ctx = new AC({ sampleRate: RATE, latencyHint: "playback" });
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
    srcNode = ctx.createMediaElementSource(video);
    srcNode.connect(ctx.destination);        // until the server answers, the page sounds exactly as before
    const code = "(" + workletMain.toString() + ")()";
    const url = URL.createObjectURL(new Blob([code], { type: "text/javascript" }));
    await ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
  }

  function makeNode() {
    if (node) { try { node.disconnect(); } catch (e) {} }
    node = new AudioWorkletNode(ctx, "mh-core", {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [2], channelCount: 2,
      channelCountMode: "explicit", channelInterpretation: "speakers",
      processorOptions: { latencyFrames, hop, capFrames: RATE * 14, gen, debug: cfg.debug },
    });
    node.port.onmessage = (e) => {
      const m = e.data;
      if (m.t === "in") {
        if (ws && ws.readyState === 1) {
          const bytes = new Uint8Array(5 + m.buf.byteLength);
          bytes[0] = 1; new DataView(bytes.buffer).setUint32(1, m.gen, true);
          bytes.set(new Uint8Array(m.buf.buffer), 5);
          ws.send(bytes);
        }
      } else if (m.t === "beep" && cfg.debug) {
        const ots = ctx.getOutputTimestamp();
        dbg.beeps.push(ots.performanceTime + (m.time - ots.contextTime) * 1000);
      } else if (m.t === "late") {
        dbg.late = m.frames;
      }
    };
    srcNode.disconnect();
    srcNode.connect(node);
    node.connect(ctx.destination);
  }

  function connect() {
    if (closed) return;
    say("connecting ...", true);
    try {
      ws = new WebSocket("ws://127.0.0.1:" + cfg.port);
    } catch (e) {
      say("cannot open a connection from this page (blocked by the site?)", true);
      return;
    }
    ws.binaryType = "arraybuffer";
    ws.onopen = () => ws.send(JSON.stringify({ type: "hello", token: cfg.token, window_ms: cfg.windowMs, margin_ms: cfg.marginMs }));
    ws.onmessage = (e) => {
      if (typeof e.data === "string") {
        const m = JSON.parse(e.data);
        if (m.type === "hello_ok") {
          hop = m.hop; margin = m.margin;
          latencyFrames = hop + margin + Math.round(cfg.budgetMs / 1000 * RATE);
          delayMs = latencyFrames / RATE * 1000;
          gen++;
          if (!node) { makeNode(); startPicture(); }
          else { node.port.postMessage({ t: "latency", latencyFrames }); node.port.postMessage({ t: "reset", gen }); }
          ws.send(JSON.stringify({ type: "reset", gen }));
          say("music removal on (picture and sound are " + (delayMs / 1000).toFixed(1) + " s behind the page)");
          log("connected: hop", hop, "margin", margin, "latency", latencyFrames, "frames");
        }
        return;
      }
      const dv = new DataView(e.data);
      if (dv.getUint8(0) !== 2) return;
      const g = dv.getUint32(1, true), k = dv.getUint32(5, true), n = dv.getUint32(9, true);
      const data = new Int16Array(e.data.slice(13));
      dbg.blocks++;
      if (node) node.port.postMessage({ t: "block", gen: g, k, n, data }, [data.buffer]);
    };
    ws.onclose = (ev) => {
      if (closed) return;
      say("lost the connection to the MuteHaram live server (code " + (ev && ev.code) + (ev && ev.reason ? ", " + ev.reason : "") +
          (ev && ev.code === 4003 ? " - wrong token: remove and re-add the extension, then try again" : "") +
          "); playing the original sound (retrying)", true);
      clearTimeout(retryTimer);
      retryTimer = setTimeout(connect, 2000);
    };
    ws.onerror = () => { /* onclose follows */ };
  }

  // a jump in the video: forget everything we were about to play
  function restart(why) {
    if (!node || !ws || ws.readyState !== 1) return;
    gen++;
    node.port.postMessage({ t: "reset", gen });
    ws.send(JSON.stringify({ type: "reset", gen }));
    while (queue.length) queue.shift().bm.close();     // old pictures must not appear after the jump
    dbg.events.push(why);
  }
  video.addEventListener("seeking", () => restart("seeking"));
  video.addEventListener("emptied", () => restart("emptied"));
  video.addEventListener("loadstart", () => restart("loadstart"));

  // sync fine-tuning: Alt+, makes the picture 40 ms earlier, Alt+. 40 ms later
  window.addEventListener("keydown", (e) => {
    if (!e.altKey || (e.key !== "," && e.key !== ".")) return;
    videoOffsetMs += e.key === "." ? 40 : -40;
    localStorage.setItem("mh_video_offset_ms", String(videoOffsetMs));
    say("picture offset " + videoOffsetMs + " ms");
  }, true);

  function toggle() {
    enabled = !enabled;
    if (!enabled) {
      closed = true; clearTimeout(retryTimer);
      try { ws && ws.close(); } catch (e) {}
      stopPicture();
      try { srcNode.disconnect(); node && node.disconnect(); srcNode.connect(ctx.destination); } catch (e) {}
      badge.remove();
      log("off");
    } else {
      location.reload();           // simplest way to get a clean slate
    }
  }

  window.__mhLive = { toggle, debug: dbg, audioInfo: () => ({ base: ctx.baseLatency, out: ctx.outputLatency, sr: ctx.sampleRate, state: ctx.state }), get delayMs() { return delayMs; }, setOffset(ms) { videoOffsetMs = ms; } };

  (async () => {
    try {
      await setupAudio();
      connect();
    } catch (e) {
      console.warn("[MuteHaram live] could not start:", e);
      say("could not start on this page: " + e.message, true);
    }
  })();
})();

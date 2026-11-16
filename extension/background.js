// Faderly browser extension.
//
// Works on its own: the toolbar popup lists tabs playing sound (Now playing / Recent)
// with pause, mute and volume; there are keyboard shortcuts, a badge with the number
// of tabs playing, and an option to let only one tab play at a time.
//
// When the Faderly desktop app is running it also reports the tabs there
// (ws://127.0.0.1:8765/ext) and carries out the app's commands.

const HUB_URL = "ws://127.0.0.1:8765/ext";
const REPORT_EVERY_MS = 1500;
const RECENT_MINUTES = 10; // tabs with no player drop out of Recent after this
const RECENT_CAP = 15;

const DEFAULT_SETTINGS = { onePlayer: false };

let ws = null;
let retryMs = 1000;
let reportTimer = null;
let settings = { ...DEFAULT_SETTINGS };
// tab id -> { lastPlayed: ms | null, dismissedAt: ms | null }. Tabs here have played sound;
// kept until the tab closes. Stored so it survives the service worker being restarted.
let meta = {};

const ready = Promise.all([
  chrome.storage.session.get("meta").then(r => { meta = r.meta || {}; }),
  chrome.storage.sync.get(DEFAULT_SETTINGS).then(s => { settings = s; }),
]);
function saveMeta() { chrome.storage.session.set({ meta }); }

function track(id) {
  if (!meta[id]) meta[id] = { lastPlayed: null, dismissedAt: null };
  return meta[id];
}

// ---- which browser is this ----------------------------------------------------
function browserKey() {
  const brands = (navigator.userAgentData?.brands || []).map(b => b.brand);
  if (brands.includes("Microsoft Edge")) return "msedge";
  if (brands.includes("Brave")) return "brave";
  if (brands.includes("Opera")) return "opera";
  return "chrome";
}

// ---- functions that run inside the page ----------------------------------------
function pageProbe() {
  return [...document.querySelectorAll("video, audio")]
    .filter(e => e.currentSrc || e.srcObject)
    .map(e => ({ paused: e.paused || e.ended, volume: e.volume, played: e.currentTime > 0, muted: e.muted,
                 time: e.currentTime, duration: Number.isFinite(e.duration) ? e.duration : 0 }));
}

function pageSetPlaying(play) {
  let els = [...document.querySelectorAll("video, audio")].filter(e => e.currentSrc || e.srcObject);
  if (!play) { els.forEach(e => e.pause()); return; }
  if (els.some(e => !e.muted)) els = els.filter(e => !e.muted);
  const best = els.sort((a, b) => b.currentTime - a.currentTime)[0];
  if (best) best.play().catch(() => {});
}

// Runs in the page's own world (MAIN), so it can reach a site's player.
function pageSetVolume(v) {
  // YouTube keeps its own saved volume and puts it back on every video; set it
  // there, so YouTube and the extension agree and nothing fights over the level.
  const yt = document.getElementById("movie_player");
  if (yt && typeof yt.setVolume === "function") {
    yt.setVolume(Math.round(v * 100));
    window.__audioHubVolume = null;
    return;
  }
  // 100% is the page's normal level: drop the override instead of enforcing it.
  window.__audioHubVolume = v < 1 ? v : null;
  document.querySelectorAll("video, audio").forEach(e => { e.volume = v; });
  if (!window.__audioHubHooked) {
    window.__audioHubHooked = true;
    // Keep a lowered level for videos that start later (next video, playlists).
    document.addEventListener("play", e => {
      if (window.__audioHubVolume != null && "volume" in e.target) e.target.volume = window.__audioHubVolume;
    }, true);
  }
}

// Scroll the media that's playing (else the one played last) into view and flash it.
function pageReveal() {
  const els = [...document.querySelectorAll("video, audio")]
    .filter(e => (e.currentSrc || e.srcObject) && e.getClientRects().length);
  if (!els.length) return false;
  const el = els.find(e => !e.paused && !e.muted) || els.find(e => !e.paused)
    || els.sort((a, b) => b.currentTime - a.currentTime)[0];
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return true; // an <audio> without controls: nothing to outline
  const box = document.createElement("div");
  Object.assign(box.style, {
    position: "absolute", left: `${r.left + scrollX}px`, top: `${r.top + scrollY}px`,
    width: `${r.width}px`, height: `${r.height}px`, zIndex: 2147483647, pointerEvents: "none",
    borderRadius: "8px", boxShadow: "0 0 0 3px #6c8cff, 0 0 24px 6px rgba(108,140,255,.6)",
    transition: "opacity .6s", opacity: "1",
  });
  document.documentElement.appendChild(box);
  // The scroll above is smooth: follow the element until it settles.
  let frames = 0;
  (function follow() {
    const q = el.getBoundingClientRect();
    box.style.left = `${q.left + scrollX}px`; box.style.top = `${q.top + scrollY}px`;
    if (++frames < 60) requestAnimationFrame(follow);
  })();
  setTimeout(() => { box.style.opacity = "0"; }, 1200);
  setTimeout(() => box.remove(), 1900);
  return true;
}

// Seek the media pageReveal/pageSetPlaying would pick: the one playing, else the furthest along.
function pageSeek(t) {
  const els = [...document.querySelectorAll("video, audio")]
    .filter(e => (e.currentSrc || e.srcObject) && Number.isFinite(e.duration) && e.duration > 0);
  const el = els.find(e => !e.paused && !e.muted) || els.find(e => !e.paused)
    || els.sort((a, b) => b.currentTime - a.currentTime)[0];
  if (el) el.currentTime = Math.max(0, Math.min(t, el.duration));
}

// Runs in the page's own world (MAIN): what Next / Previous can do in this frame.
function pageTrackCaps() {
  const h = window.__audioHubMS || {};
  const yt = document.querySelector(".ytp-next-button");
  const player = document.getElementById("movie_player");
  return {
    next: !!h.nexttrack || !!(yt && yt.offsetParent),
    prev: !!h.previoustrack,
    // YouTube evens out loudness, so its <video> volume isn't the level the user set.
    volume: player && typeof player.getVolume === "function" ? player.getVolume() / 100 : null,
  };
}

// Next / Previous: the page's own handler; else YouTube's Next button; Previous
// without a handler restarts the track, as most players do.
function pageTrack(dir) {
  const h = window.__audioHubMS || {};
  const action = dir === "next" ? "nexttrack" : "previoustrack";
  if (h[action]) {
    try { h[action]({ action }); return; } catch {}
  }
  if (dir === "next") {
    const b = document.querySelector(".ytp-next-button");
    if (b && b.offsetParent) b.click();
    return;
  }
  const els = [...document.querySelectorAll("video, audio")].filter(e => e.currentSrc || e.srcObject);
  const el = els.find(e => !e.paused) || els.sort((a, b) => b.currentTime - a.currentTime)[0];
  if (el) el.currentTime = 0;
}

async function inPage(tabId, func, args = [], world = "ISOLATED") {
  try {
    const results = await chrome.scripting.executeScript({ target: { tabId, allFrames: true }, func, args, world });
    return results.map(r => r.result).filter(Boolean);
  } catch {
    return []; // chrome:// pages, the Web Store, discarded tabs...
  }
}

async function mediaState(tabId) {
  let els = (await inPage(tabId, pageProbe)).flat();
  // Ignore silent extras such as YouTube's muted hover previews when there's a real player.
  const unmuted = els.filter(e => !e.muted);
  if (unmuted.length) els = unmuted;
  if (!els.length) return { hasMedia: false };
  const playing = els.filter(e => !e.paused);
  const main = playing[0] || els.find(e => e.played) || els[0];
  const caps = await inPage(tabId, pageTrackCaps, [], "MAIN");
  const siteVolume = caps.find(c => c.volume != null)?.volume;
  return { hasMedia: true, paused: playing.length === 0, volume: siteVolume ?? main.volume,
           position: main.time, duration: main.duration, // duration 0: live stream or unknown
           canNext: caps.some(c => c.next),
           canPrev: true }; // without a page handler, Previous restarts the track
}

function site(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; }
}

// ---- the list of tabs with sound ------------------------------------------------
async function collect() {
  await ready;
  const now = Date.now();
  const tabs = await chrome.tabs.query({});
  const alive = new Set(tabs.map(t => t.id));
  for (const id of Object.keys(meta)) if (!alive.has(Number(id))) delete meta[id];
  for (const t of tabs) if (t.audible) track(t.id);

  const out = await Promise.all(tabs.filter(t => meta[t.id]).map(async t => {
    const media = await mediaState(t.id);
    const playing = media.hasMedia ? !media.paused : !!t.audible;
    const m = meta[t.id];
    if (playing) m.lastPlayed = now;
    return {
      id: t.id, windowId: t.windowId,
      title: t.title || "", site: site(t.url), favIconUrl: t.favIconUrl || "",
      audible: !!t.audible, muted: !!t.mutedInfo?.muted,
      playing, lastPlayed: m.lastPlayed, dismissedAt: m.dismissedAt,
      ...media,
    };
  }));
  saveMeta();
  return out;
}

// Same rules as the desktop app: Now playing, then Recent newest first.
function split(tabs) {
  const now = Date.now();
  const playing = tabs.filter(t => t.playing);
  const recent = tabs.filter(t => !t.playing
    && !(t.dismissedAt && (t.lastPlayed || 0) <= t.dismissedAt)
    && (t.hasMedia || (t.lastPlayed && now - t.lastPlayed < RECENT_MINUTES * 60000)));
  recent.sort((a, b) => (b.lastPlayed || 0) - (a.lastPlayed || 0));
  return { playing, recent: recent.slice(0, RECENT_CAP) };
}

// Tabs that were paused before the extension started still count.
async function scanExistingTabs() {
  await ready;
  const tabs = await chrome.tabs.query({ url: ["http://*/*", "https://*/*"], discarded: false });
  await Promise.all(tabs.map(async t => {
    const els = (await inPage(t.id, pageProbe)).flat();
    if (els.some(e => (e.played || !e.paused) && !e.muted)) track(t.id);
  }));
  saveMeta();
}

// ---- actions (from the popup, shortcuts or the desktop app) ------------------------
async function pauseTab(id) { await inPage(id, pageSetPlaying, [false]); }

async function pauseAll(exceptId) {
  const tabs = await collect();
  await Promise.all(tabs.filter(t => t.playing && t.hasMedia && t.id !== exceptId).map(t => pauseTab(t.id)));
}

async function act(c) {
  const id = c.tabId;
  switch (c.cmd) {
    case "mute":
      await chrome.tabs.update(id, { muted: !!c.value });
      break;
    case "playpause": {
      const state = await mediaState(id);
      if (state.hasMedia) await inPage(id, pageSetPlaying, [state.paused]);
      break;
    }
    case "play": { // play for sure: the hub's Resume all
      const state = await mediaState(id);
      if (state.hasMedia && state.paused) await inPage(id, pageSetPlaying, [true]);
      break;
    }
    case "pause": { // pause for sure; playpause above is a toggle
      const state = await mediaState(id);
      if (state.hasMedia) {
        await inPage(id, pageSetPlaying, [false]);
      } else {
        // Web Audio / games make sound without a media element: nothing to
        // pause, so mute the tab to silence it.
        await chrome.tabs.update(id, { muted: true });
      }
      break;
    }
    case "next":
    case "prev":
      await inPage(id, pageTrack, [c.cmd], "MAIN");
      break;
    case "seek":
      await inPage(id, pageSeek, [Math.max(0, Number(c.value) || 0)]);
      break;
    case "volume":
      await inPage(id, pageSetVolume, [Math.max(0, Math.min(1, Number(c.value)))], "MAIN");
      break;
    case "close": // ✕ in the app or popup; Ctrl+Shift+T brings it back
      await chrome.tabs.remove(id);
      break;
    case "dismiss":
      await ready;
      track(id).dismissedAt = Date.now();
      saveMeta();
      break;
    case "focus": {
      // Window first, tab second: switching tabs in a window that isn't showing yet
      // (minimized, covered) leaves a stale frame up until Chrome repaints it.
      const tab = await chrome.tabs.get(id);
      const win = await chrome.windows.get(tab.windowId);
      await chrome.windows.update(tab.windowId,
        win.state === "minimized" ? { focused: true, state: "normal" } : { focused: true });
      await chrome.tabs.update(id, { active: true });
      await inPage(id, pageReveal);
      break;
    }
    case "solo": // keep this tab, pause the rest
      await pauseAll(id);
      break;
    case "pauseAll":
      await pauseAll();
      break;
    case "resetAll": // the popup's Reset: every listed tab back to 100% and unmuted
      await Promise.all((await collect()).map(async t => {
        if (t.muted) await chrome.tabs.update(t.id, { muted: false });
        if (t.hasMedia && (t.volume ?? 1) < 1) await inPage(t.id, pageSetVolume, [1], "MAIN");
      }));
      break;
  }
  reportSoon();
}

// Play/pause the tab you'd most likely mean: one that's playing, else the latest paused one.
async function toggleLatest() {
  const { playing, recent } = split(await collect());
  if (playing.length) {
    await Promise.all(playing.filter(t => t.hasMedia).map(t => pauseTab(t.id)));
  } else {
    const last = recent.find(t => t.hasMedia);
    if (last) await act({ cmd: "playpause", tabId: last.id });
  }
}

// ---- badge: how many tabs are making sound -------------------------------------------
async function updateBadge() {
  const n = (await chrome.tabs.query({ audible: true })).length;
  chrome.action.setBadgeText({ text: n ? String(n) : "" });
  chrome.action.setBadgeBackgroundColor({ color: "#2fb862" });
}

// ---- desktop app connection --------------------------------------------------------
async function report() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  const tabs = await collect();
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "tabs", tabs }));
}

let soon = null;
function reportSoon() {
  clearTimeout(soon);
  soon = setTimeout(report, 150);
}

function hubConnected() { return !!ws && ws.readyState === WebSocket.OPEN; }

// Connected to the desktop app (only ever on 127.0.0.1), the extension always shares
// its tabs; there's no switch for it. The popup works on its own when the app is off.
const VERSION = chrome.runtime.getManifest().version;

function startReporting() {
  clearInterval(reportTimer);
  if (!hubConnected()) return;
  scanExistingTabs().then(report);
  reportTimer = setInterval(report, REPORT_EVERY_MS); // also keeps the worker awake
}

// A new tab in the window you're using, scrolled to Faderly, rather than whatever
// window a command-line launch would pick.
async function openExtensionsPage() {
  const win = await chrome.windows.getLastFocused({ windowTypes: ["normal"] }).catch(() => null);
  const tab = await chrome.tabs.create({ url: `chrome://extensions/?id=${chrome.runtime.id}`,
                                         ...(win ? { windowId: win.id } : {}) });
  await chrome.windows.update(tab.windowId, { focused: true });
}

async function connect() {
  await ready;
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const sock = ws = new WebSocket(HUB_URL);
  sock.onopen = () => {
    retryMs = 1000;
    sock.send(JSON.stringify({ type: "hello", browser: browserKey(), version: VERSION,
                               accepts: ["reload", "openExtensionsPage"] }));
    startReporting();
  };
  sock.onmessage = e => {
    let c;
    try { c = JSON.parse(e.data); } catch { return; }
    if (c.cmd === "reload") chrome.runtime.reload();  // the app's "Update now": re-read this folder
    else if (c.cmd === "openExtensionsPage") openExtensionsPage();
    else act(c).catch(() => {});
  };
  sock.onclose = () => {
    if (ws !== sock) return;
    clearInterval(reportTimer);
    ws = null;
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 30000);
  };
  sock.onerror = () => {}; // onclose follows and retries
}

// ---- messages from the popup ----------------------------------------------------------
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    if (msg.type === "list") {
      await ready;
      reply({ ...split(await collect()), hub: hubConnected(), settings });
    } else if (msg.type === "cmd") {
      await act(msg);
      reply({ ok: true });
    } else if (msg.type === "settings") {
      settings = { ...settings, ...msg.value };
      await chrome.storage.sync.set(settings);
      reply({ settings });
    }
  })();
  return true; // reply comes later
});

// ---- events ----------------------------------------------------------------------------
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  if ("audible" in info) {
    updateBadge();
    if (info.audible) {
      await ready;
      track(tabId).lastPlayed = Date.now();
      saveMeta();
      if (settings.onePlayer) pauseAll(tabId); // a tab started: pause the others
    }
  }
  if ("audible" in info || "mutedInfo" in info || "title" in info) reportSoon();
});
chrome.tabs.onRemoved.addListener(() => { updateBadge(); reportSoon(); });

chrome.commands.onCommand.addListener(cmd => {
  if (cmd === "toggle-latest") toggleLatest();
  else if (cmd === "pause-all") pauseAll();
});

// The worker can be stopped while Faderly isn't running; this wakes it to try again.
chrome.alarms.create("audiohub-reconnect", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(() => { connect(); scanExistingTabs(); hookExistingTabs(); updateBadge(); });

// Tabs open before install/reload never ran the Next/Previous hook; pages that set
// their handlers again (YouTube does on each video) are picked up from then on.
async function hookExistingTabs() {
  const tabs = await chrome.tabs.query({ url: ["http://*/*", "https://*/*"], discarded: false });
  await Promise.all(tabs.map(t => chrome.scripting.executeScript({
    target: { tabId: t.id, allFrames: true }, files: ["mediasession-hook.js"], world: "MAIN",
  }).catch(() => {})));
}
connect();
updateBadge();

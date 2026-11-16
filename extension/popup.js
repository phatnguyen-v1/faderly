// Popup mixer: asks the background worker for the tab list every second and sends it
// commands. Rows look and work like the desktop app's: icon, play/pause, previous/next,
// a progress bar to seek, and the volume folded behind a speaker button.

const $ = id => document.getElementById(id);
let dragging = null;

const send = msg => chrome.runtime.sendMessage(msg);
const cmd = (c, tabId, value) => send({ type: "cmd", cmd: c, tabId, value }).then(refresh);

// Same icon set as the desktop app, drawn in currentColor.
const I = (body, fill) => `<svg viewBox="0 0 16 16" aria-hidden="true" fill="${fill ? "currentColor" : "none"}"
  stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${body}</svg>`;
const ICON = {
  play:  I(`<path d="M5 3.5v9l7-4.5z"/>`, true),
  pause: I(`<rect x="4" y="3.5" width="2.5" height="9" rx=".6"/><rect x="9.5" y="3.5" width="2.5" height="9" rx=".6"/>`, true),
  prev:  I(`<path d="M12 3.5v9L6 8z" fill="currentColor"/><path d="M4 3.5v9"/>`),
  next:  I(`<path d="M4 3.5v9L10 8z" fill="currentColor"/><path d="M12 3.5v9"/>`),
  x:     I(`<path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/>`),
  vol:   I(`<path d="M2.5 6h2.5l3.5-3v10L5 10H2.5z" fill="currentColor"/><path d="M11 5.5a3.5 3.5 0 0 1 0 5M12.8 3.8a6 6 0 0 1 0 8.4"/>`),
  muted: I(`<path d="M2.5 6h2.5l3.5-3v10L5 10H2.5z" fill="currentColor"/><path d="M11 6l3.5 4M14.5 6L11 10"/>`),
  solo:  I(`<circle cx="8" cy="8" r="2.5" fill="currentColor"/><circle cx="8" cy="8" r="5.5"/>`),
  reset: I(`<path d="M2.5 8a5.5 5.5 0 1 0 1.6-3.9"/><path d="M2.5 2.5v3h3"/>`),
};

function ago(ms) {
  if (!ms) return "";
  const s = (Date.now() - ms) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function fmt(t) {
  t = Math.max(0, Math.floor(t));
  const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = String(t % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

function el(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}

function setIconHTML(btn, key, html) {
  if (btn.dataset.icon !== key) { btn.dataset.icon = key; btn.innerHTML = html; }
}

function makeRow(id) {
  const n = el(`<div class="row">
    <div class="top">
      <span class="appicon"></span>
      <div class="info" title="Go to this tab"><div class="name"></div><div class="sub"></div></div>
      <div class="btns">
        <button class="prev" title="Previous">${ICON.prev}</button>
        <button class="pp"></button>
        <button class="next" title="Next">${ICON.next}</button>
        <button class="solo" title="Pause the other tabs">${ICON.solo}</button>
        <button class="volbtn"></button>
        <button class="x" title="Close tab">${ICON.x}</button>
      </div>
    </div>
    <div class="prog" hidden><span class="t t0"></span><input class="seek" type="range" min="0" step="1"><span class="t t1"></span></div>
  </div>`);
  n.querySelector(".info").onclick = () => cmd("focus", id).then(() => window.close());
  n.querySelector(".pp").onclick = () => cmd("playpause", id);
  n.querySelector(".prev").onclick = () => cmd("prev", id);
  n.querySelector(".next").onclick = () => cmd("next", id);
  n.querySelector(".solo").onclick = () => cmd("solo", id);
  n.querySelector(".x").onclick = e => { e.stopPropagation(); cmd("close", id); };  // Ctrl+Shift+T undoes it
  n.querySelector(".volbtn").onclick = e => { e.stopPropagation(); toggleVolPop(n, id, e.currentTarget); };

  const seek = n.querySelector(".seek");
  seek.addEventListener("pointerdown", () => { dragging = seek; });
  seek.addEventListener("input", () => paintProgress(n, Number(seek.value)));
  seek.addEventListener("change", () => {
    if (dragging === seek) dragging = null;
    const v = Number(seek.value);
    send({ type: "cmd", cmd: "seek", tabId: id, value: v });
    if (n._p) Object.assign(n._p, { pos: v, at: performance.now() });
  });
  return n;
}

function setIcon(n, t) {
  const box = n.querySelector(".appicon");
  const url = t.favIconUrl && !(box._failed || new Set()).has(t.favIconUrl) ? t.favIconUrl : "";
  const letter = (t.site || t.title || "?").charAt(0).toUpperCase();
  if (box.dataset.src === url && (url || box.textContent === letter)) return;
  box.dataset.src = url;
  box.classList.toggle("img", !!url);
  if (!url) { box.textContent = letter; return; }
  const img = new Image();
  img.alt = "";
  img.onerror = () => {
    (box._failed ||= new Set()).add(url);
    box.dataset.src = ""; box.classList.remove("img"); box.textContent = letter;
  };
  img.src = url;
  box.replaceChildren(img);
}

// Position comes with each refresh (once a second); in between the bar runs on by itself.
function updateProgress(n, t) {
  const prog = n.querySelector(".prog");
  const has = t.hasMedia && t.duration > 0 && t.position != null;
  prog.hidden = !has;
  if (!has) { n._p = null; return; }
  const seek = prog.querySelector(".seek");
  seek.max = Math.floor(t.duration);
  const now = performance.now(), p = n._p;
  const guess = p ? p.pos + (p.playing ? (now - p.at) / 1000 : 0) : null;
  if (!p || !t.playing || !p.playing || Math.abs(guess - t.position) > 1.5 || p.dur !== t.duration)
    n._p = { pos: t.position, at: now, dur: t.duration, playing: t.playing };
  if (dragging !== seek) paintProgress(n);
}

function paintProgress(n, at) {
  const p = n._p; if (!p) return;
  const pos = at ?? Math.min(p.dur, p.pos + (p.playing ? (performance.now() - p.at) / 1000 : 0));
  const seek = n.querySelector(".seek");
  if (at == null) seek.value = Math.floor(pos);
  seek.style.setProperty("--p", (pos / p.dur * 100).toFixed(2) + "%");
  n.querySelector(".t0").textContent = fmt(pos);
  n.querySelector(".t1").textContent = fmt(p.dur);
}

setInterval(() => {
  for (const n of document.querySelectorAll(".row"))
    if (n._p && n._p.playing && dragging !== n.querySelector(".seek")) paintProgress(n);
}, 250);

function updateRow(n, t, section, othersPlaying) {
  setIcon(n, t);
  n.querySelector(".name").textContent = t.title || t.site;
  n.querySelector(".name").title = t.title;
  const status = t.playing ? "" : (t.hasMedia ? "paused" : "stopped");
  n.querySelector(".sub").textContent =
    [t.site, status, section === "recent" ? ago(t.lastPlayed) : ""].filter(Boolean).join(" · ");

  const pp = n.querySelector(".pp");
  pp.hidden = !t.hasMedia;
  setIconHTML(pp, t.playing ? "pause" : "play", t.playing ? ICON.pause : ICON.play);
  pp.title = t.playing ? "Pause" : "Play";
  n.querySelector(".prev").hidden = !(t.hasMedia && t.canPrev);
  n.querySelector(".next").hidden = !(t.hasMedia && t.canNext);
  n.querySelector(".solo").hidden = !(t.playing && othersPlaying);

  // Volume: folded behind the speaker button, which shows the level only when it isn't 100%.
  const pct = Math.round((t.volume ?? 1) * 100);
  const vb = n.querySelector(".volbtn");
  const state = `${!!t.muted}|${t.hasMedia ? pct : 100}`;
  if (vb.dataset.state !== state) {
    vb.dataset.state = state;
    vb.innerHTML = (t.muted ? ICON.muted : ICON.vol)
      + (!t.muted && t.hasMedia && pct < 100 ? `<span class="lvl">${pct}%</span>` : "");
    vb.classList.toggle("on", !!t.muted);
    vb.title = t.muted ? "Muted · click for volume" : `Volume ${t.hasMedia ? pct : 100}%`;
  }
  n._vol = { pct, muted: !!t.muted, hasMedia: !!t.hasMedia };
  if (volFor && volFor.n === n) paintVolPop(n._vol);

  updateProgress(n, t);
}

// ---- a tab's volume: one floating panel with a vertical slider, opened from the
// speaker button, so it can't be mistaken for the progress bar.
const volPop = el(`<div class="volpop" hidden role="dialog" aria-label="Volume">
  <span class="pct"></span>
  <input class="vslider" type="range" min="0" max="100" aria-label="Volume">
  <button class="mute"></button>
</div>`);
document.body.appendChild(volPop);
let volFor = null; // {n, id, btn}
const vslider = volPop.querySelector(".vslider"), vmute = volPop.querySelector(".mute");
vslider.addEventListener("pointerdown", () => { dragging = vslider; });
vslider.addEventListener("change", () => { if (dragging === vslider) dragging = null; });
vslider.addEventListener("input", () => {
  volPop.querySelector(".pct").textContent = vslider.value + "%";
  if (volFor) send({ type: "cmd", cmd: "volume", tabId: volFor.id, value: vslider.value / 100 });
});
vmute.onclick = () => volFor && cmd("mute", volFor.id, !vmute.classList.contains("on"));
volPop.addEventListener("click", e => e.stopPropagation());
volPop.addEventListener("wheel", e => {
  e.preventDefault();
  if (vslider.hidden) return;
  vslider.value = Math.max(0, Math.min(100, Number(vslider.value) + (e.deltaY < 0 ? 5 : -5)));
  vslider.dispatchEvent(new Event("input"));
}, { passive: false });
document.addEventListener("click", () => closeVolPop());
document.addEventListener("keydown", e => { if (e.key === "Escape") closeVolPop(); });

function paintVolPop(v) {
  vslider.hidden = !v.hasMedia;  // no media element: only the tab mute works
  volPop.querySelector(".pct").textContent = v.hasMedia ? v.pct + "%" : "";
  if (dragging !== vslider) vslider.value = v.pct;
  vmute.classList.toggle("on", v.muted);
  setIconHTML(vmute, String(v.muted), v.muted ? ICON.muted : ICON.vol);
  vmute.title = v.muted ? "Unmute tab" : "Mute tab";
}
function toggleVolPop(n, id, btn) {
  if (volFor && volFor.n === n) return closeVolPop();
  closeVolPop();
  volFor = { n, id, btn };
  paintVolPop(n._vol);
  btn.classList.add("open");
  volPop.hidden = false;
  // Below the button; above if there's no room; beside it when the popup is short.
  const r = btn.getBoundingClientRect(), w = volPop.offsetWidth, h = volPop.offsetHeight;
  const H = document.documentElement.clientHeight, W = document.documentElement.clientWidth;
  const clampX = x => Math.max(4, Math.min(W - w - 4, x)), clampY = y => Math.max(4, Math.min(H - h - 4, y));
  let x = clampX(r.left + r.width / 2 - w / 2), y;
  if (r.bottom + 6 + h <= H - 4) y = r.bottom + 6;
  else if (r.top - 6 - h >= 4) y = r.top - 6 - h;
  else { x = clampX(r.left - 6 - w); y = clampY(r.top + r.height / 2 - h / 2); }
  volPop.style.left = x + "px";
  volPop.style.top = y + "px";
}
function closeVolPop() {
  if (!volFor) return;
  volFor.btn.classList.remove("open");
  volFor = null;
  volPop.hidden = true;
  if (dragging === vslider) dragging = null;
}

// Keyed update so rows aren't rebuilt while you click or drag.
function sync(container, tabs, section, othersPlaying) {
  const cache = container._nodes || (container._nodes = new Map());
  const seen = new Set();
  tabs.forEach((t, i) => {
    let n = cache.get(t.id);
    if (!n) { n = makeRow(t.id); cache.set(t.id, n); }
    updateRow(n, t, section, othersPlaying);
    if (container.children[i] !== n) container.insertBefore(n, container.children[i] || null);
    seen.add(t.id);
  });
  for (const [id, n] of cache) if (!seen.has(id)) {
    if (volFor && volFor.n === n) closeVolPop();
    n.remove(); cache.delete(id);
  }
  $(section + "Empty").hidden = tabs.length > 0;
}

// Now playing keeps the order tabs appeared in, so rows don't jump under the mouse.
let playOrder = [];
function steady(playing) {
  const ids = playing.map(t => t.id);
  playOrder = playOrder.filter(id => ids.includes(id)).concat(ids.filter(id => !playOrder.includes(id)));
  return playOrder.map(id => playing.find(t => t.id === id));
}

async function refresh() {
  const s = await send({ type: "list" });
  if (!s) return;
  const playing = steady(s.playing);
  sync($("playing"), playing, "playing", playing.length > 1);
  sync($("recent"), s.recent, "recent", false);
  $("pauseAll").hidden = !playing.some(t => t.hasMedia);
  const all = [...s.playing, ...s.recent];
  $("resetAll").hidden = !all.some(t => t.muted || (t.hasMedia && (t.volume ?? 1) < 1));
  const hub = $("hub");
  hub.classList.toggle("on", s.hub);
  hub.textContent = s.hub ? "Desktop app connected" : "Desktop app not running";
  $("onePlayer").checked = s.settings.onePlayer;
}

$("pauseAll").innerHTML = ICON.pause + "<span>Pause all</span>";
$("resetAll").innerHTML = ICON.reset + "<span>Reset</span>";
$("pauseAll").onclick = () => cmd("pauseAll");
$("resetAll").onclick = () => cmd("resetAll");
$("onePlayer").onchange = e => send({ type: "settings", value: { onePlayer: e.target.checked } }).then(refresh);
$("shortcuts").onclick = e => { e.preventDefault(); chrome.tabs.create({ url: "chrome://extensions/shortcuts" }); };

refresh();
setInterval(refresh, 1000);

// Runs in the page's own JavaScript world at document_start (see manifest.json).
// Pages hand their Next / Previous track handlers to navigator.mediaSession; Chrome
// gives extensions no way to call them, so keep a copy where the extension can.
(() => {
  const ms = navigator.mediaSession;
  if (!ms || window.__audioHubMS) return;
  const handlers = {};
  Object.defineProperty(window, "__audioHubMS", { value: handlers });
  const set = ms.setActionHandler.bind(ms);
  ms.setActionHandler = (action, fn) => {
    if (fn) handlers[action] = fn; else delete handlers[action];
    return set(action, fn);
  };
})();

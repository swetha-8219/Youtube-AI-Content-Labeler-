// Service worker: the only part of the extension that talks to the backend.
// (Content scripts are bound by youtube.com's CORS; the worker uses host_permissions.)
const DEFAULT_API = "http://localhost:8787";

async function apiBase() {
  const { apiBase } = await chrome.storage.sync.get("apiBase");
  return (apiBase || DEFAULT_API).replace(/\/+$/, "");
}

// Random per-install ID for report rate limiting. Not linked to any account.
async function clientId() {
  let { clientId } = await chrome.storage.local.get("clientId");
  if (!clientId) {
    clientId = crypto.randomUUID();
    await chrome.storage.local.set({ clientId });
  }
  return clientId;
}

async function call(path, init) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 10000);
  try {
    const r = await fetch((await apiBase()) + path, { ...init, signal: ctrl.signal });
    const body = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, body };
  } finally {
    clearTimeout(t);
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  (async () => {
    try {
      if (msg.type === "verify") {
        sendResponse(await call("/verify?videoId=" + encodeURIComponent(msg.videoId)));
      } else if (msg.type === "report") {
        sendResponse(await call("/report", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ videoId: msg.videoId, clientId: await clientId() })
        }));
      } else if (msg.type === "feedback") {
        sendResponse(await call("/feedback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ videoId: msg.videoId, level: msg.level, clientId: await clientId() })
        }));
      } else {
        sendResponse({ ok: false, error: "unknown message" });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e && e.name || e) });
    }
  })();
  return true; // async response
});

const input = document.getElementById("api");
const status = document.getElementById("status");

chrome.storage.sync.get("apiBase").then(({ apiBase }) => {
  input.value = apiBase || "http://localhost:8787";
});

document.getElementById("save").onclick = async () => {
  const url = input.value.trim().replace(/\/+$/, "");
  await chrome.storage.sync.set({ apiBase: url });
  status.textContent = "Saved. Testing…";
  try {
    const r = await fetch(url + "/health");
    const h = await r.json();
    const lines = Object.entries(h.checks).map(([k, v]) => `${v ? "✓" : "–"} ${k}`);
    status.textContent = "Connected.\n" + lines.join("\n") +
      "\n\nIf you use a URL other than localhost:8787, add it to host_permissions in manifest.json.";
  } catch (e) {
    status.textContent = "Could not reach the backend: " + e;
  }
};

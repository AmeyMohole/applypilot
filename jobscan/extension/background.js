// Content scripts cannot use chrome.downloads, so they ask this worker to save the file.
//
// Preferred: POST the capture, as JSON, to the receiver the jobscan pipeline runs on 127.0.0.1. It
// writes the file itself, overwriting the last run's, so there is no Save dialog and nobody has to click.
// Fallback (the pipeline is not running, e.g. you pressed the panel button by hand): a normal download.
// That fallback can show Chrome's "Save As" dialog if "Ask where to save each file" is on in
// chrome://settings/downloads; turn that off if you use the panel by hand.
const RECEIVER = "http://127.0.0.1:8765";

async function viaReceiver(msg) {
  const res = await fetch(`${RECEIVER}/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: msg.name, ext: msg.ext, text: msg.text }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || !body.ok) throw new Error(body.error || `receiver answered ${res.status}`);
  return body;
}

function viaDownload(msg, sendResponse) {
  // A data: URL is used because blob URLs made in a content script are not reliably readable here.
  const url = "data:text/plain;charset=utf-8," + encodeURIComponent(msg.text);
  const ext = msg.ext === "log" ? "log" : "txt";
  chrome.downloads.download(
    { url, filename: `jobscan/${msg.name}.${ext}`, conflictAction: "overwrite", saveAs: false },
    (id) => sendResponse({ ok: !chrome.runtime.lastError, id, via: "download", error: chrome.runtime.lastError?.message })
  );
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type !== "save") return;
  viaReceiver(msg)
    .then((body) => sendResponse({ ok: true, via: "receiver", file: body.file }))
    .catch(() => viaDownload(msg, sendResponse));
  return true; // keep the channel open for the async response
});

// LinkedIn's router can rewrite the address (dropping #jobscan-auto) before the page script runs.
// So catch the marker here, at the moment the tab is created, and leave a flag for the page script.
const flagAuto = (url) => {
  if (url && url.includes("jobscan-auto")) chrome.storage.local.set({ autoPending: Date.now() });
};
chrome.tabs.onCreated.addListener((tab) => flagAuto(tab.pendingUrl || tab.url));
chrome.tabs.onUpdated.addListener((_id, info) => flagAuto(info.url));

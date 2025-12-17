const PARENT_ORIGIN = "https://audiovisualevents.com.au";

console.log("iframe helper loaded");

let lastSentSrc = "";

function isEmbedded() {
  return window.top !== window.self;
}

function postMoodboard() {
  // ✅ key fix: when opened standalone, don't try to postMessage
  if (!isEmbedded()) return;

  const img = document.querySelector("img.previewImg");
  const src = img?.src || "";
  if (!src) return;

  // If you only want data URLs, keep this:
  // if (!src.startsWith("data:image/")) return;

  if (src === lastSentSrc) return;
  lastSentSrc = src;

  window.parent.postMessage(
    { type: "MOODBOARD_DATA_URL", dataUrl: src },
    PARENT_ORIGIN
  );
}

window.addEventListener("load", postMoodboard);

const mo = new MutationObserver(postMoodboard);
mo.observe(document.documentElement, {
  subtree: true,
  childList: true,
  attributes: true,
  attributeFilter: ["src"],
});

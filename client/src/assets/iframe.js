const PARENT_ORIGIN = "https://audiovisualevents.com.au";

console.log("iframe helper loaded");

let lastSentSrc = "";

function postMoodboard() {
  const img = document.querySelector(".previewImg");
  const src = img?.src || "";
  if (!src) return;

  // If you ONLY want data URLs, keep this line. Otherwise delete it.
  // if (!src.startsWith("data:image/")) return;

  if (src === lastSentSrc) return;
  lastSentSrc = src;

  window.parent.postMessage(
    { type: "MOODBOARD_IMAGE_SRC", src },
    PARENT_ORIGIN
  );
}

window.addEventListener("load", postMoodboard);

const mo = new MutationObserver(postMoodboard);
mo.observe(document.documentElement, {
  subtree: true,
  childList: true,
  attributes: true,
  attributeFilter: ["src"]
});
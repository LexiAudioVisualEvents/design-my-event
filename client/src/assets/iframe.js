<script>
  const PARENT_ORIGIN = "https://audiovisualevents.com.au";

  function postMoodboard() {
    const img = document.querySelector("img.previewImg");
    if (!img?.src) return;
    if (!img.src.startsWith("data:image/")) return;

    window.parent.postMessage(
      { type: "MOODBOARD_DATA_URL", dataUrl: img.src },
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
</script>
// Injected into the active tab via chrome.scripting.executeScript({func:
// extractPage}). Runs in the page's isolated world -- plain DOM only, no
// chrome.* APIs, no closures over anything outside this function (the
// function body is serialized and re-executed in that world, so any outer
// reference would be undefined there).
export function extractPage() {
  const selectionText = String(window.getSelection ? window.getSelection() : "").trim();

  const root =
    document.querySelector("article, main, [role='main']") || document.body;
  const clone = root.cloneNode(true);
  clone
    .querySelectorAll(
      "script,style,noscript,svg,iframe,canvas,nav,header,footer,aside,form," +
        "[aria-hidden='true'],[hidden],.sr-only"
    )
    .forEach((node) => node.remove());

  const text = (clone.innerText || "")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
    .slice(0, 200000);

  return {
    url: location.href,
    title: document.title || "",
    text,
    selection: selectionText.slice(0, 20000),
  };
}

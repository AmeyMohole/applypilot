// Click "...more" on every post in the current LinkedIn search results, so the contact email
// (usually at the bottom of a long post) is visible before you copy the page.
//
// Use: scroll the results first, press F12 -> Console, paste this whole file, press Enter.
// It runs only in this tab, only when you paste it, and pauses 0.5-1.2 s between clicks.
// If the console asks you to type "allow pasting", do that first.
(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // Match by visible text, not CSS class (class names change; the words don't).
  const norm = (el) => (el.innerText || "").replace(/\s+/g, "").toLowerCase();
  const isMore = (el) => ["…more", "...more", "…seemore"].includes(norm(el));
  let clicked = 0;
  for (const el of document.querySelectorAll("main button, main span[role='button']")) {
    if (!isMore(el)) continue;
    el.scrollIntoView({ block: "center" });
    el.click();
    clicked++;
    await sleep(500 + Math.random() * 700);
  }
  console.log(`Expanded ${clicked} posts. Now Ctrl+A, Ctrl+C and paste into captures/*.txt`);
})();

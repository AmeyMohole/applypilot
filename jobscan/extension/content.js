// jobscan capture panel. Runs only on LinkedIn post-search pages, only after you press a button,
// only in the foreground tab, and with human-paced pauses. Captures are sent to the jobscan pipeline in the background (no Save dialog).
(() => {
  if (window.__jobscan) return;
  window.__jobscan = true;

  // Short queries beat long ones (each returns ~10-15 posts), and "india" keeps out the US/Vietnam posts.
  // 30 keywords, searched 20 at a time: the first PINNED run every time (cloud and devops come first),
  // the rest rotate, so every keyword is covered within two runs.
  // Keep this list identical to LINKEDIN_QUERIES in core/queries.py (python doctor.py checks it).
  const DEFAULT_QUERIES = [
    "cloud engineer aws hiring india",
    "devops engineer hiring india",
    "aws devops engineer hiring india",
    "site reliability engineer hiring india",
    "production support engineer hiring india",
    "cloud production support hiring india",
    "application production support hiring india",
    "full stack developer hiring india",
    "software engineer python react hiring india",
    "python developer hiring india",
    "software developer hiring india",
    "cloud engineer hiring pune",
    "cloud engineer hiring mumbai",
    "devops engineer hiring nashik",
    "devops engineer hiring pune",
    "production support engineer hiring pune",
    "production support engineer hiring mumbai",
    "terraform aws engineer hiring india",
    "kubernetes devops hiring india",
    "aws cloud support engineer hiring india",
    "full stack developer hiring pune",
    "devsecops engineer hiring india",
    "python backend developer hiring india",
    "backend developer hiring india",
    "infrastructure engineer hiring india",
    "platform engineer hiring india",
    "python devops engineer hiring india",
    "cloud devops engineer hiring india",
    "python developer hiring pune",
    "python developer hiring mumbai",
  ];
  const PINNED = 2;
  // Hard limits: this is the part that keeps the account safe, so they are not settings.
  const MAX_QUERIES = 20;
  const MAX_ROUNDS = 8;          // ~10-15 posts per query is all search returns anyway
  const STAGNANT_LIMIT = 3;      // stop after 3 rounds with no new content
  const MAX_RUNS_PER_DAY = 3;
  const ROTATING = MAX_QUERIES - PINNED;

  // The queries for run number `rot`: the pinned ones plus the next ROTATING from the rest, wrapping.
  const windowAt = (rot) => {
    const rest = DEFAULT_QUERIES.slice(PINNED);
    const take = Array.from({ length: ROTATING }, (_, i) => rest[(rot + i) % rest.length]);
    return DEFAULT_QUERIES.slice(0, PINNED).concat(take);
  };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rand = (a, b) => a + Math.random() * (b - a);
  const norm = (el) => (el.innerText || "").replace(/\s+/g, "").toLowerCase();
  const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
  const store = {
    get: (k) => new Promise((r) => chrome.storage.local.get(k, (v) => r(v[k]))),
    set: (k, v) => new Promise((r) => chrome.storage.local.set({ [k]: v }, r)),
  };
  const searchUrl = (q) =>
    "https://www.linkedin.com/search/results/content/?keywords=" + encodeURIComponent(q) +
    "&datePosted=%22past-24h%22&sortBy=%22date_posted%22&origin=FACETED_SEARCH";
  const mainEl = () => document.querySelector("main") || document.body;
  const currentQuery = () => new URLSearchParams(location.search).get("keywords") || "page";

  // ---------------------------------------------------------------- panel
  const panel = document.createElement("div");
  panel.style.cssText =
    "position:fixed;right:16px;bottom:16px;z-index:2147483647;width:270px;padding:10px;" +
    "background:#fff;color:#111;border:1px solid #888;border-radius:8px;font:12px Arial;" +
    "box-shadow:0 2px 10px rgba(0,0,0,.3)";
  panel.innerHTML =
    '<b>jobscan capture</b><br>' +
    '<textarea id="js-q" rows="8" style="width:100%;box-sizing:border-box;margin:6px 0"></textarea>' +
    '<button id="js-run">Run all queries</button> <button id="js-one">This page only</button> ' +
    '<button id="js-stop">Stop</button><div id="js-status" style="margin-top:6px;white-space:pre-wrap"></div>';
  document.body.appendChild(panel);
  const $ = (id) => panel.querySelector(id);
  // The panel offers this run's window; `ready` resolves once it is filled in (it depends on storage).
  const ready = (async () => {
    const rot = (await store.get("rot")) || 0;
    $("#js-q").value = windowAt(rot).join("\n");
  })();
  const status = (msg) => { $("#js-status").textContent = msg; };

  // ---------------------------------------------------------------- capture
  async function waitForPosts() {
    for (let i = 0; i < 20; i++) {
      if (mainEl().innerText.length > 2500) return true;
      await sleep(1000);
    }
    return false;
  }

  // LinkedIn does not always scroll the window; find the element that actually scrolls.
  function scroller() {
    for (let el = mainEl(); el && el !== document.body; el = el.parentElement) {
      const oy = getComputedStyle(el).overflowY;
      if ((oy === "auto" || oy === "scroll") && el.scrollHeight > el.clientHeight + 50) return el;
    }
    return document.scrollingElement || document.documentElement;
  }
  const isPage = (s) => s === document.scrollingElement || s === document.documentElement;
  const viewH = (s) => (isPage(s) ? window.innerHeight : s.clientHeight);
  const atBottom = (s) => s.scrollTop + viewH(s) >= s.scrollHeight - 5;

  // Walk down in reader-sized steps (a jump straight to the bottom can skip the lazy-load trigger).
  async function scrollAll() {
    const s = scroller();
    const info = `${s.tagName.toLowerCase()}${isPage(s) ? "(page)" : ""} height ${s.scrollHeight}`;
    let stagnant = 0, last = 0, rounds = 0;
    for (; rounds < MAX_ROUNDS && stagnant < STAGNANT_LIMIT; rounds++) {
      for (let step = 0; !atBottom(s) && step < 40; step++) {
        s.scrollBy({ top: rand(500, 800) });
        await sleep(rand(500, 900));
      }
      await sleep(rand(1300, 2600));             // let the next batch load
      for (const b of mainEl().querySelectorAll("button")) {
        if (["showmoreresults", "seemoreresults"].includes(norm(b))) { b.click(); await sleep(rand(1300, 2600)); }
      }
      const size = mainEl().innerText.length;
      stagnant = size - last < 200 ? stagnant + 1 : 0;
      last = size;
      status(`Scrolling... round ${rounds + 1} (${(mainEl().innerText.match(/^\s*Feed post\s*$/gm) || []).length} posts)`);
    }
    return { rounds, info: `${info} -> ${s.scrollHeight}` };
  }

  // Recruiters put the contact email at the very bottom of long posts, behind "...more".
  // Match by visible text (class names change), with all whitespace stripped.
  async function expandAll() {
    let n = 0;
    for (const el of mainEl().querySelectorAll("button, span[role='button']")) {
      if (!["…more", "...more", "…seemore"].includes(norm(el))) continue;
      el.scrollIntoView({ block: "center" });
      el.click();
      n++;
      status(`Expanding posts... ${n}`);
      await sleep(rand(500, 1200));
    }
    return n;
  }

  function grabText() {
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.selectAllChildren(mainEl()); // same text as Ctrl+A, Ctrl+C
    let text = sel.toString();
    sel.removeAllRanges();
    if (!text.trim()) text = mainEl().innerText;
    return text;
  }

  async function capturePage() {
    window.scrollTo(0, 0);
    if (!(await waitForPosts())) status("Page looks empty; capturing anyway.");
    const { rounds, info } = await scrollAll();
    const expanded = await expandAll();
    const text = grabText();
    const posts = (text.match(/^\s*Feed post\s*$/gm) || []).length;
    const header = [`# jobscan capture`, `# url: ${location.href}`, `# at: ${new Date().toISOString()}`,
      `# rounds: ${rounds}  posts: ${posts}  expanded: ${expanded}`, `# scroller: ${info}`, ""].join("\n");
    return { text: header + "\n" + text, rounds, expanded, posts };
  }

  const saveFile = (name, text, ext = "txt") =>
    new Promise((r) => chrome.runtime.sendMessage({ type: "save", name, text, ext }, r));

  const stamp = () => new Date().toISOString().replace(/[-:T]/g, "").slice(0, 12);

  // ---------------------------------------------------------------- runs
  async function finish(msg) {
    const run = (await store.get("run")) || {};
    const wasActive = run.active;
    run.active = false;
    await store.set("run", run);
    status(msg);
    // The daily Python run waits for this marker (a .log file, which scan.py never reads as a capture).
    if (wasActive && run.stamp) await saveFile(`${run.stamp}-done`, `status: ${msg}\n`, "log");
  }

  async function startRun() {
    await ready;
    const queries = [...new Set($("#js-q").value.split("\n").map((s) => s.trim()).filter(Boolean))]
      .slice(0, MAX_QUERIES);
    if (!queries.length) return status("Add at least one query.");
    const today = new Date().toDateString();
    const runs = (await store.get("runs")) || { day: today, n: 0 };
    if (runs.day !== today) { runs.day = today; runs.n = 0; }
    if (runs.n >= MAX_RUNS_PER_DAY)
      return status(`Already ran ${MAX_RUNS_PER_DAY} times today. Stopping here keeps the account safe.`);
    runs.n++;
    await store.set("runs", runs);
    const rot = (await store.get("rot")) || 0;       // the next run moves on to the next keywords
    await store.set("rot", (rot + ROTATING) % (DEFAULT_QUERIES.length - PINNED));
    await store.set("run", { active: true, queries, idx: 0, stamp: stamp(), nav: 0, t: Date.now() });
    status(`Starting ${queries.length} queries...`);
    if (currentQuery() === queries[0]) continueRun(); else location.href = searchUrl(queries[0]);
  }

  async function continueRun() {
    let run = await store.get("run");
    if (!run || !run.active) return;
    if (Date.now() - (run.t || 0) > 20 * 60 * 1000)   // abandoned run: never resume on its own
      return finish("Old unfinished run ignored. Press 'Run all queries' to start again.");
    run.t = Date.now();
    await store.set("run", run);
    if (/checkpoint|authwall|\/login/.test(location.href))
      return finish("LinkedIn asked for a login or verification. Stopped; sort it out by hand.");
    const q = run.queries[run.idx];
    if (currentQuery() !== q) {            // landed somewhere unexpected: retry twice, then stop
      if (++run.nav > 2) return finish("Could not reach the search page. Stopped.");
      await store.set("run", run);
      location.href = searchUrl(q);
      return;
    }
    status(`Query ${run.idx + 1}/${run.queries.length}: ${q}`);
    const cap = await capturePage();
    const res = await saveFile(`${run.stamp}-${run.idx + 1}-${slug(q)}`, cap.text);
    if (!res || !res.ok) return finish(`Save failed: ${res && res.error}`);
    run = await store.get("run");          // Stop may have been pressed meanwhile
    if (!run.active) return status("Stopped.");
    run.idx++; run.nav = 0;
    if (run.idx >= run.queries.length) return finish(`Done. ${run.queries.length} captures saved. Now run: python 1-find/scan.py`);
    await store.set("run", run);
    const wait = rand(8000, 15000);        // a person does not fire searches back to back
    status(`Saved (${cap.posts} posts, ${cap.expanded} expanded). Next query in ${Math.round(wait / 1000)}s...`);
    await sleep(wait);
    run = await store.get("run");
    if (run && run.active) location.href = searchUrl(run.queries[run.idx]);
  }

  $("#js-run").onclick = startRun;
  $("#js-stop").onclick = () => finish("Stopped.");
  $("#js-one").onclick = async () => {
    const cap = await capturePage();
    const res = await saveFile(`${stamp()}-page-${slug(currentQuery())}`, cap.text);
    status(res && res.ok
      ? `Saved: ${cap.posts} posts, ${cap.expanded} expanded.` + (cap.posts ? "" : "\nWARNING: no 'Feed post' markers found; tell Claude.")
      : `Save failed: ${res && res.error}`);
  };

  // The daily startup script opens LinkedIn with #jobscan-auto, which starts the run by itself,
  // once per day. Everything else (limits, pacing, stop-on-login-wall) is identical to a button press.
  (async () => {
    await continueRun(); // resumes after each navigation of an active run
    const run = await store.get("run");
    if (run && run.active) return;
    const pending = await store.get("autoPending");              // set by background.js
    const flagged = pending && Date.now() - pending < 5 * 60 * 1000;
    if (!flagged && !location.hash.includes("jobscan-auto")) return;
    await store.set("autoPending", 0);                           // one trigger per launch
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
    const today = new Date().toDateString();
    const auto = (await store.get("auto")) || {};
    const n = auto.day === today ? auto.n : 0;
    if (n >= 2) return status("Both automatic runs for today are used up.");
    await store.set("auto", { day: today, n: n + 1 });
    await sleep(rand(4000, 8000)); // let the page settle like a person opening it
    startRun();
  })();
})();

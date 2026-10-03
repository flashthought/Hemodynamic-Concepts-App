// Hash router: one web app, three concepts, plus evidence and design file.
import { api, el, STATIC } from "./api.js";
import { mountLive } from "./live.js";
import { mountReview } from "./review.js";
import { mountDesign, mountEvidence, mountOverview } from "./pages.js";

const main = document.getElementById("main");
let meta = null;
let dispose = null;

async function route() {
  if (dispose) { try { dispose(); } catch { /* page already gone */ } dispose = null; }
  const hash = location.hash.replace(/^#/, "") || "/";
  const [, page, sub] = hash.split("/");
  document.querySelectorAll(".nav a").forEach((a) => {
    const r = a.dataset.route;
    a.classList.toggle("active", r === "/" ? hash === "/" : hash.startsWith(r));
    a.toggleAttribute("aria-current", a.classList.contains("active"));
  });
  try {
    if (!meta) meta = await api("/api/meta");
    if (page === "concept-a") dispose = mountLive(main, "A", meta);
    else if (page === "concept-b") dispose = mountLive(main, "B", meta);
    else if (page === "concept-c") dispose = mountReview(main, meta);
    else if (page === "evidence") await mountEvidence(main);
    else if (page === "design") await mountDesign(main, sub);
    else await mountOverview(main, meta);
  } catch (e) {
    main.replaceChildren(el("div", { class: "card" }, el("h1", {}, "Something went wrong"), el("p", {}, e.message),
      el("p", { class: "muted" }, "Reload the page; if the problem persists, check the server console.")));
  }
  main.focus({ preventScroll: true });
}

async function init() {
  if (STATIC) {
    document.querySelector(".intended-use")?.append(" ",
      el("strong", {}, "Public demo:"),
      " every output was pre-computed with the verified Version 5 model on held-out recordings; nothing runs on a server.");
  }
  try {
    meta = await api("/api/meta");
    const m = meta.model;
    document.getElementById("model-badge").textContent =
      `${m.model_file.replace(".pth", "")} · ${m.device} · ${m.sha256.slice(0, 8)}`;
  } catch (e) {
    document.getElementById("model-badge").textContent = "model unavailable";
  }
  window.addEventListener("hashchange", route);
  route();
}

init();

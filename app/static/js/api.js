import { staticApi, staticHref } from "./static-api.js";

// True on the public, server-free build (scripts/build_static.py).
export const STATIC = Boolean(window.HEMO_STATIC);

// Link to a downloadable /api resource (report, CSV, JSON) in either mode.
export const href = (path) => (STATIC ? staticHref(path) : path);

// Thin fetch wrapper: every server error becomes a plain-language Error
// (Nielsen: help users recognise, diagnose and recover from errors).
export async function api(path, { method = "GET", body } = {}) {
  if (STATIC) return staticApi(path, { method, body });
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Cannot reach the app server. Check that it is still running.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
  return data;
}

export const fmt = {
  num: (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d)),
  pct: (v, d = 0) => (v === null || v === undefined ? "—" : `${(100 * v).toFixed(d)}%`),
  time: (s) => {
    if (s === null || s === undefined) return "--:--";
    const t = Math.max(0, Math.round(s));
    return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
  },
};

// Create an element with attributes and children; text is always set via
// textContent, so server-provided strings can never inject markup.
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export const LEVEL = {
  STABLE: { colour: "#2e7d32", label: "STABLE", priority: "No alarm" },
  WATCH: { colour: "#0277bd", label: "WATCH", priority: "Low priority" },
  DETERIORATING: { colour: "#f2a900", label: "DETERIORATING", priority: "Medium priority" },
  CRITICAL: { colour: "#c62828", label: "CRITICAL", priority: "High priority" },
  "SIGNAL CHECK": { colour: "#546e7a", label: "SIGNAL CHECK", priority: "Technical alarm" },
};
export const LEVEL_BY_INDEX = ["STABLE", "WATCH", "DETERIORATING", "CRITICAL"];

// Shape per level so the alert never relies on colour alone.
export function levelIcon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 40 40");
  svg.setAttribute("aria-hidden", "true");
  const shapes = {
    STABLE: '<circle cx="20" cy="20" r="16" fill="none" stroke="currentColor" stroke-width="4"/><path d="M12 20l6 6 11-12" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"/>',
    WATCH: '<circle cx="20" cy="20" r="16" fill="none" stroke="currentColor" stroke-width="4"/><circle cx="20" cy="20" r="5" fill="currentColor"/>',
    DETERIORATING: '<path d="M20 4l17 30H3z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><path d="M20 15v9" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="20" cy="29" r="2.4" fill="currentColor"/>',
    CRITICAL: '<path d="M13 3h14l10 10v14L27 37H13L3 27V13z" fill="none" stroke="currentColor" stroke-width="4" stroke-linejoin="round"/><path d="M20 11v12" stroke="currentColor" stroke-width="4" stroke-linecap="round"/><circle cx="20" cy="29" r="2.4" fill="currentColor"/>',
    "SIGNAL CHECK": '<rect x="4" y="4" width="32" height="32" rx="6" fill="none" stroke="currentColor" stroke-width="4"/><path d="M10 22h5l3-8 4 14 3-6h5" fill="none" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/>',
  };
  svg.innerHTML = shapes[name] || shapes.STABLE; // static markup only, never server data
  return svg;
}

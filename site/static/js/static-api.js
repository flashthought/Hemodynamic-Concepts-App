// Static-hosting adapter: answers the app's /api/* calls from files pre-computed
// by scripts/build_static.py, so the same UI runs without the Python server.
// Only active when window.HEMO_STATIC is true (set in the built site/index.html).

const cache = new Map();
const sessions = new Map();

async function load(url) {
  if (!cache.has(url)) {
    cache.set(url, fetch(url).then(async (r) => {
      if (!r.ok) throw new Error(`Pre-computed data not found (${r.status}).`);
      return r.json();
    }).catch((e) => { cache.delete(url); throw e; }));
  }
  return cache.get(url);
}

const startKey = (v) => (v === null || v === undefined ? "begin" : String(Math.round(Number(v) * 10) / 10));  // = start_key() in build_static.py

function attachWaveforms(tick, w, concept) {
  const i1 = Math.round(tick.t * w.fs);
  const i0 = i1 - Math.round(10 * w.fs);
  return {
    ...tick,
    waveforms: {
      fs: w.fs,
      ecg: w.ecg.slice(i0, i1),
      ppg: w.ppg.slice(i0, i1),
      abp: concept === "A" ? w.abp.slice(i0, i1) : null,
    },
  };
}

export async function staticApi(path, { method = "GET", body } = {}) {
  if (method === "GET") {
    const fixed = { "/api/meta": "meta", "/api/recordings": "recordings", "/api/verification": "verification",
                    "/api/selection": "selection", "/api/docs": "docs/index" };
    if (fixed[path]) return load(`/data/${fixed[path]}.json`);
    let m = path.match(/^\/api\/docs\/([\w-]+)$/);
    if (m) return load(`/data/docs/${m[1]}.json`);
    m = path.match(/^\/api\/review\/([\w-]+)$/);
    if (m) return load(`/data/review/${m[1]}/index.json`);
    throw new Error("Not available in the static demo.");
  }
  if (method === "DELETE") {
    sessions.delete(path.split("/").pop());
    return { ok: true };
  }
  if (path === "/api/session") {
    const { recording_id: recId, concept, start_sec: start } = body;
    const [{ recordings }, data, waves] = await Promise.all([
      load("/data/recordings.json"),
      load(`/data/sessions/${recId}_${concept}_${startKey(start)}.json`),
      load(`/data/waves/${recId}.json`),
    ]);
    const sid = Math.random().toString(16).slice(2).padEnd(32, "0").slice(0, 32);
    sessions.set(sid, { ticks: data.ticks, waves, concept, i: 0 });
    return { session_id: sid, concept, recording: recordings.find((r) => r.id === recId) };
  }
  let m = path.match(/^\/api\/session\/(\w+)\/(step|event)$/);
  if (m) {
    const s = sessions.get(m[1]);
    if (!s) throw new Error("Session expired - start playback again.");
    if (m[2] === "event") {
      throw new Error("Marking interventions needs the live model server. This public demo replays the recorded pump/EHR treatment events.");
    }
    const n = Math.max(1, Math.min(Number(body?.n) || 1, 60));
    const out = s.ticks.slice(s.i, s.i + n).map((k) => ({ ...k }));
    s.i += out.length;
    if (out.length) out[out.length - 1] = attachWaveforms(out[out.length - 1], s.waves, s.concept);
    return { ticks: out, finished: s.i >= s.ticks.length };
  }
  throw new Error("Not available in the static demo.");
}

// Download / report links (anchors cannot go through fetch).
export function staticHref(path) {
  const m = path.match(/^\/api\/review\/([\w-]+)\/(report\.html|timeline\.csv|review\.json)$/);
  return m ? `/data/review/${m[1]}/${m[2]}` : path;
}

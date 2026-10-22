"use strict";
// 伺服器的 Open-Meteo 額度被擋（雲端主機共用 IP）時，改由瀏覽器代為下載資料：
// 伺服器回 409 + 要下載的請求清單 → 瀏覽器用自己的 IP 下載 → 回傳 /api/relay → 重試原本的請求
async function apiFetch(url, opts = {}, onRelay) {
  if (window.WX_STATIC) return staticFetch(url);
  for (let round = 1; round <= 12; round++) {
    const r = await fetch(url, opts);
    if (r.status !== 409) return r;
    const { need } = await r.json();
    onRelay?.(round, need.length);
    const results = [];
    for (let i = 0; i < need.length; i += 2) {   // Open-Meteo 限制同時連線數，兩個兩個抓
      const got = await Promise.all(need.slice(i, i + 2).map(fetchSpec));
      results.push(...got.filter(Boolean));
    }
    if (!results.length) throw new Error("資料來源暫時無法連線（你的網路的免費額度也可能已用完），請稍後再試");
    for (let i = 0; i < results.length; i += 8) {  // 分批上傳，避免單次太大
      const up = await fetch("/api/relay", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ results: results.slice(i, i + 8) }),
      });
      if (!up.ok) throw new Error("資料回傳伺服器失敗");
    }
  }
  throw new Error("資料下載次數過多，請重新整理後再試");
}

async function fetchSpec(spec) {
  try {
    const body = new URLSearchParams(spec.params);
    const r = spec.method === "POST"
      ? await fetch(spec.url, { method: "POST", body })
      : await fetch(`${spec.url}?${body}`);
    if (!r.ok) return null;
    return { id: spec.id, data: await r.json() };
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- 靜態資料模式（Claude Artifact 版）
// Artifact 頁面不能連外部網站：API 改為讀取跟頁面一起發佈的預先計算檔案 data/*.json
const jsonResponse = (obj) => new Response(JSON.stringify(obj), { status: 200, headers: { "Content-Type": "application/json" } });

async function staticFetch(url) {
  const u = new URL(url, location.href), path = u.pathname, q = u.searchParams;
  const presets = window.WX_STATIC.presets;
  if (path.endsWith("/api/geocode")) {
    const s = (q.get("q") || "").trim();
    return jsonResponse(presets.filter((p) => !s || p.name.includes(s) || s.includes(p.name) || p.admin1.includes(s)));
  }
  if (path.endsWith("/api/forecast")) {
    const lat = +q.get("lat"), lon = +q.get("lon");
    const p = presets.reduce((a, b) => (Math.hypot(b.latitude - lat, b.longitude - lon) < Math.hypot(a.latitude - lat, a.longitude - lon) ? b : a));
    return fetch(`data/forecast-${p.id}.json`);
  }
  const files = { "/api/map/grid": "grid.json", "/api/map/cyclones": "cyclones.json", "/api/event/taiwan": "event.json" };
  const key = Object.keys(files).find((k) => path.endsWith(k));
  if (key) return fetch(`data/${files[key]}`);
  return new Response(JSON.stringify({ detail: "此功能在離線版本無法使用" }), { status: 404 });
}

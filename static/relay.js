"use strict";
// 伺服器的 Open-Meteo 額度被擋（雲端主機共用 IP）時，改由瀏覽器代為下載資料：
// 伺服器回 409 + 要下載的請求清單 → 瀏覽器用自己的 IP 下載 → 回傳 /api/relay → 重試原本的請求
async function apiFetch(url, opts = {}, onRelay) {
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

"use strict";
// 颱風／豪雨事件：各縣市災害程度與停班停課機率
const TaiwanEvent = (() => {
  const P_STEPS = [[0.9, "#9e1a1a"], [0.7, "#d9442b"], [0.5, "#f08c3a"], [0.3, "#f6c955"], [0.1, "#a9cdee"], [0, "#e6eef7"]];
  const L_COLORS = ["#e6eaef", "#ffe08a", "#ffad5c", "#ec5f3b", "#8f1c22"];
  const esc_ = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pc = (v) => `${Math.round((v || 0) * 100)}%`;
  let ev = null, dayIdx = 0, mode = "prob", selected = null, map = null, geo = null, layer = null;

  const pColor = (p) => P_STEPS.find(([t]) => p >= t)[1];
  const colorOf = (c) => (mode === "prob" ? pColor(c.probability) : L_COLORS[c.level]);
  // 日期一律以台灣時間計算（日期字串直接拆解，不經過瀏覽器時區）
  const dayName = (d) => {
    const [y, m, dd] = d.split("-").map(Number);
    const target = Date.UTC(y, m - 1, dd), todayTW = new Date(Date.now() + 8 * 3600e3);
    const diff = Math.round((target - Date.UTC(todayTW.getUTCFullYear(), todayTW.getUTCMonth(), todayTW.getUTCDate())) / 864e5);
    const wk = "日一二三四五六"[new Date(target).getUTCDay()];
    return `${diff === 1 ? "明天" : diff === 2 ? "後天" : "週" + wk} ${m}/${dd}`;
  };
  // 小縣市標籤位置微調，避免數字重疊
  const LABEL_AT = { A: [25.07, 121.56], F: [24.93, 121.68], C: [25.2, 121.8], O: [24.83, 120.86], J: [24.68, 121.17],
    I: [23.5, 120.33], Q: [23.38, 120.62], M: [23.85, 120.95], H: [24.9, 121.2], E: [22.95, 120.6], T: [22.5, 120.62] };

  async function load() {
    try {
      const r = await apiFetch("/api/event/taiwan");
      if (!r.ok) return;
      ev = await r.json();
    } catch { return; }
    renderCard();
  }

  function renderCard() {
    const box = document.getElementById("event-card");
    if (!ev || !ev.active || !ev.days.length) { box.hidden = true; return; }
    const d0 = ev.days[0], top = d0.counties.slice(0, 3);
    const s = ev.storms[0];
    const near = s ? `預報最接近台灣約 ${s.closest.distance_km} km（${new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", month: "numeric", day: "numeric", hour: "2-digit", hour12: false }).format(new Date(s.closest.time))}）` : "";
    box.hidden = false;
    box.innerHTML = `
      <div class="ev-icon">${ev.kind === "typhoon" ? "🌀" : "🌧️"}</div>
      <div class="ev-body">
        <div class="ev-title">${esc_(ev.title)} 事件 <span class="ev-tag">進行中</span></div>
        <div class="ev-sub">${esc_(near)}</div>
        <div class="ev-top">${dayName(d0.date)}停班停課機率最高：${top.map((c) => `<b>${esc_(c.name)} ${pc(c.probability)}</b>`).join("、")}</div>
      </div>
      <button class="ev-open">查看各縣市災害程度與停班停課機率</button>`;
    box.querySelector(".ev-open").addEventListener("click", open);
  }

  function open() {
    const dlg = document.getElementById("event-dialog");
    dlg.innerHTML = `
      <div class="evd-head">
        <div><h2>${esc_(ev.title)} 事件 · 各縣市影響評估</h2><p class="muted">更新 ${esc_(ev.updated.replace("T", " "))} · 依 82 組系集預報估計</p></div>
        <button class="evd-close" aria-label="關閉">✕</button>
      </div>
      <div class="evd-ctrl">
        <div class="seg" id="evd-days">${ev.days.map((d, i) => `<button data-i="${i}" class="${i === dayIdx ? "on" : ""}">${dayName(d.date)}</button>`).join("")}</div>
        <div class="seg" id="evd-mode"><button data-m="prob" class="${mode === "prob" ? "on" : ""}">停班停課機率</button><button data-m="level" class="${mode === "level" ? "on" : ""}">災害程度</button></div>
      </div>
      <div class="evd-grid">
        <div class="evd-mapwrap"><div id="evd-map"></div><div class="evd-legend" id="evd-legend"></div></div>
        <div class="evd-side"><div id="evd-detail"></div><div id="evd-list"></div></div>
      </div>
      <div class="evd-notes"><p>${esc_(ev.criteria)}</p><ul>${ev.notes.map((n) => `<li>${esc_(n)}</li>`).join("")}</ul>
        <a href="${esc_(ev.official_link)}" target="_blank" rel="noopener">行政院人事行政總處・停班停課公告 ↗</a></div>`;
    dlg.querySelector(".evd-close").addEventListener("click", () => dlg.close());
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    dlg.querySelectorAll("#evd-days button").forEach((b) => b.addEventListener("click", () => { dayIdx = +b.dataset.i; refresh(); }));
    dlg.querySelectorAll("#evd-mode button").forEach((b) => b.addEventListener("click", () => { mode = b.dataset.m; refresh(); }));
    dlg.showModal();
    map = null;
    initMap();
  }

  async function initMap() {
    map = L.map("evd-map", { zoomSnap: 0.25, attributionControl: true, scrollWheelZoom: false });
    if (!window.WX_STATIC) L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", { attribution: "底圖 © Esri", maxNativeZoom: 16 }).addTo(map);
    map.fitBounds([[21.85, 118.2], [26.4, 122.1]]);
    map.on("zoomend", () => ev && drawLabels());
    if (!geo) {
      try {
        const topo = await (await fetch(window.WX_STATIC ? "data/taiwan-counties.json" : "https://cdn.jsdelivr.net/npm/taiwan-atlas@1.0.1/counties-10t.json")).json();
        geo = topojson.feature(topo, topo.objects.counties);
      } catch { geo = null; }
    }
    refresh();
    setTimeout(() => map.invalidateSize(), 50);
  }

  function counties() { return ev.days[dayIdx].counties; }

  function refresh() {
    const dlg = document.getElementById("event-dialog");
    dlg.querySelectorAll("#evd-days button").forEach((b) => b.classList.toggle("on", +b.dataset.i === dayIdx));
    dlg.querySelectorAll("#evd-mode button").forEach((b) => b.classList.toggle("on", b.dataset.m === mode));
    const byId = Object.fromEntries(counties().map((c) => [c.id, c]));
    if (layer) layer.remove();
    layer = L.layerGroup().addTo(map);
    if (geo) {
      L.geoJSON(geo, {
        style: (f) => { const c = byId[f.properties.COUNTYID]; return { color: "#fff", weight: 1.2, fillColor: c ? colorOf(c) : "#ccc", fillOpacity: 0.88 }; },
        onEachFeature: (f, lyr) => {
          const c = byId[f.properties.COUNTYID];
          if (!c) return;
          lyr.on("click", () => select(c.id));
          lyr.bindTooltip(`${esc_(c.name)}：${mode === "prob" ? pc(c.probability) : c.level_name}`, { sticky: true });
        },
      }).addTo(layer);
    }
    drawLabels();
    renderLegend();
    renderList();
    renderDetail();
  }

  // 縣市數字標籤：依機率高低優先放置，與已放的標籤重疊就略過（放大地圖後會再出現）
  let labelLayer = null;
  function drawLabels() {
    if (labelLayer) labelLayer.remove();
    labelLayer = L.layerGroup().addTo(map);
    const placed = [];
    [...counties()].sort((a, b) => b.probability - a.probability || b.level - a.level).forEach((c) => {
      const ll = LABEL_AT[c.id] || [c.lat, c.lon], pt = map.latLngToContainerPoint(ll);
      if (placed.some((q) => Math.abs(q.x - pt.x) < 34 && Math.abs(q.y - pt.y) < 15)) return;
      placed.push(pt);
      L.marker(ll, { interactive: false, icon: L.divIcon({ className: "evd-lbl", html: `<span>${mode === "prob" ? pc(c.probability) : c.level}</span>`, iconSize: [36, 16], iconAnchor: [18, 8] }) }).addTo(labelLayer);
    });
  }

  function renderLegend() {
    const el = document.getElementById("evd-legend");
    el.innerHTML = mode === "prob"
      ? [...P_STEPS].reverse().map(([t, c], i, a) => `<span><i style="background:${c}"></i>${Math.round(t * 100)}${i < a.length - 1 ? "–" + Math.round(a[i + 1][0] * 100) : "+"}%</span>`).join("")
      : L_COLORS.map((c, i) => `<span><i style="background:${c}"></i>${i} ${["無", "輕度", "中度", "嚴重", "極嚴重"][i]}</span>`).join("");
  }

  function renderList() {
    const list = [...counties()].sort((a, b) => (mode === "prob" ? b.probability - a.probability : b.level - a.level || b.probability - a.probability));
    const el = document.getElementById("evd-list");
    el.innerHTML = `<table class="evd-table"><tr><th>縣市</th><th>停班停課機率</th><th>災害程度</th></tr>${list.map((c) => `
      <tr data-id="${c.id}" class="${c.id === selected ? "on" : ""}"><td>${esc_(c.name)}</td>
      <td><div class="pbar"><i style="width:${c.probability * 100}%;background:${pColor(c.probability)}"></i></div><span>${pc(c.probability)}</span></td>
      <td><span class="lv" style="background:${L_COLORS[c.level]};color:${c.level >= 3 ? "#fff" : "#222"}">${c.level_name}</span></td></tr>`).join("")}</table>`;
    el.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => select(tr.dataset.id)));
  }

  function renderDetail() {
    const el = document.getElementById("evd-detail");
    const c = counties().find((x) => x.id === selected);
    if (!c) { el.innerHTML = `<p class="muted">點地圖或下方列表中的縣市，查看風雨預估細節。</p>`; return; }
    el.innerHTML = `<div class="evd-card">
      <div class="evd-card-h"><b>${esc_(c.name)}</b><span class="lv" style="background:${L_COLORS[c.level]};color:${c.level >= 3 ? "#fff" : "#222"}">災害程度：${c.level_name}</span></div>
      <div class="evd-big">${pc(c.probability)}<small>停班停課機率（估計）</small></div>
      <table>
        <tr><th>最大陣風（中位數／偏強情境）</th><td>${c.gust.median} / ${c.gust.p90} km/h</td></tr>
        <tr><th>最大平均風</th><td>${c.wind.median} / ${c.wind.p90} km/h</td></tr>
        <tr><th>日累積雨量</th><td>${c.rain.median} / ${c.rain.p90} mm</td></tr>
        <tr><th>因風力達標的機率</th><td>${pc(c.p_wind)}</td></tr>
        <tr><th>因雨量達標的機率</th><td>${pc(c.p_rain)}</td></tr>
      </table>
      <div class="evd-hz">${c.hazards.length ? c.hazards.map((h) => `<span>${esc_(h)}</span>`).join("") : "<span class=ok>無明顯災害風險</span>"}</div>
      <p class="muted">偏強情境 = 系集中第 90 百分位（10% 的預報比這更強）。</p></div>`;
  }

  function select(id) {
    selected = id;
    renderList();
    renderDetail();
  }

  return { load };
})();
TaiwanEvent.load();

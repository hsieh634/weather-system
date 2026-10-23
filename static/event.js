"use strict";
// 颱風／豪雨事件：各縣市災害程度與停班停課機率
const TaiwanEvent = (() => {
  const P_STEPS = [[0.9, "#9e1a1a"], [0.7, "#d9442b"], [0.5, "#f08c3a"], [0.3, "#f6c955"], [0.1, "#a9cdee"], [0, "#e6eef7"]];
  const L_COLORS = ["#e6eaef", "#ffe08a", "#ffad5c", "#ec5f3b", "#8f1c22"];
  const esc_ = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pc = (v) => `${Math.round((v || 0) * 100)}%`;
  let ev = null, dayIdx = 0, mode = "prob", basis = "rule", selected = null, map = null, geo = null, layer = null;

  const pColor = (p) => P_STEPS.find(([t]) => p >= t)[1];
  // 目前採用的機率：法規標準，或加入歷年公告與縣市長風格校正後的機率
  const P = (c) => (basis === "style" && c.p_style != null ? c.p_style : c.probability);
  const colorOf = (c) => (mode === "prob" ? pColor(P(c)) : L_COLORS[c.level]);
  const STATUS = { full: "停班停課", half: "半天停班停課", partial: "部分地區停班停課", normal: "照常上班上課" };
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
        ${ev.style_model ? `<div class="seg" id="evd-basis"><button data-b="rule" class="${basis === "rule" ? "on" : ""}">法規標準</button><button data-b="style" class="${basis === "style" ? "on" : ""}">🎲 套用縣市長風格</button></div>` : ""}
      </div>
      <div id="evd-review"></div>
      <div class="evd-grid">
        <div class="evd-mapwrap"><div id="evd-map"></div><div class="evd-legend" id="evd-legend"></div></div>
        <div class="evd-side"><div id="evd-detail"></div><div id="evd-list"></div></div>
      </div>
      <div class="evd-notes"><p>${esc_(ev.criteria)}</p>${modelNote()}<ul>${ev.notes.map((n) => `<li>${esc_(n)}</li>`).join("")}</ul>
        <a href="${esc_(ev.official_link)}" target="_blank" rel="noopener">行政院人事行政總處・停班停課公告 ↗</a></div>`;
    dlg.querySelector(".evd-close").addEventListener("click", () => dlg.close());
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
    dlg.querySelectorAll("#evd-days button").forEach((b) => b.addEventListener("click", () => { dayIdx = +b.dataset.i; refresh(); }));
    dlg.querySelectorAll("#evd-mode button").forEach((b) => b.addEventListener("click", () => { mode = b.dataset.m; refresh(); }));
    dlg.querySelectorAll("#evd-basis button").forEach((b) => b.addEventListener("click", () => { basis = b.dataset.b; refresh(); }));
    renderReview();
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
    dlg.querySelectorAll("#evd-basis button").forEach((b) => b.classList.toggle("on", b.dataset.b === basis));
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
          lyr.bindTooltip(`${esc_(c.name)}：${mode === "prob" ? pc(P(c)) : c.level_name}`, { sticky: true });
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
    [...counties()].sort((a, b) => P(b) - P(a) || b.level - a.level).forEach((c) => {
      const ll = LABEL_AT[c.id] || [c.lat, c.lon], pt = map.latLngToContainerPoint(ll);
      if (placed.some((q) => Math.abs(q.x - pt.x) < 34 && Math.abs(q.y - pt.y) < 15)) return;
      placed.push(pt);
      L.marker(ll, { interactive: false, icon: L.divIcon({ className: "evd-lbl", html: `<span>${mode === "prob" ? pc(P(c)) : c.level}</span>`, iconSize: [36, 16], iconAnchor: [18, 8] }) }).addTo(labelLayer);
    });
  }

  function renderLegend() {
    const el = document.getElementById("evd-legend");
    el.innerHTML = mode === "prob"
      ? [...P_STEPS].reverse().map(([t, c], i, a) => `<span><i style="background:${c}"></i>${Math.round(t * 100)}${i < a.length - 1 ? "–" + Math.round(a[i + 1][0] * 100) : "+"}%</span>`).join("")
      : L_COLORS.map((c, i) => `<span><i style="background:${c}"></i>${i} ${["無", "輕度", "中度", "嚴重", "極嚴重"][i]}</span>`).join("");
  }

  function renderList() {
    const list = [...counties()].sort((a, b) => (mode === "prob" ? P(b) - P(a) : b.level - a.level || P(b) - P(a)));
    const el = document.getElementById("evd-list");
    el.innerHTML = `<table class="evd-table"><tr><th>縣市</th><th>${basis === "style" ? "風格校正機率" : "停班停課機率"}</th><th>災害程度</th></tr>${list.map((c) => `
      <tr data-id="${c.id}" class="${c.id === selected ? "on" : ""}"><td>${esc_(c.name)}${basis === "style" && c.mayor ? `<small class="mayor">${esc_(c.mayor)}</small>` : ""}</td>
      <td><div class="pbar"><i style="width:${P(c) * 100}%;background:${pColor(P(c))}"></i></div><span>${pc(P(c))}</span></td>
      <td><span class="lv" style="background:${L_COLORS[c.level]};color:${c.level >= 3 ? "#fff" : "#222"}">${c.level_name}</span></td></tr>`).join("")}</table>`;
    el.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => select(tr.dataset.id)));
  }

  function renderDetail() {
    const el = document.getElementById("evd-detail");
    const c = counties().find((x) => x.id === selected);
    if (!c) { el.innerHTML = `<p class="muted">點地圖或下方列表中的縣市，查看風雨預估細節。</p>`; return; }
    el.innerHTML = `<div class="evd-card">
      <div class="evd-card-h"><b>${esc_(c.name)}</b><span class="lv" style="background:${L_COLORS[c.level]};color:${c.level >= 3 ? "#fff" : "#222"}">災害程度：${c.level_name}</span></div>
      <div class="evd-big">${pc(P(c))}<small>${basis === "style" ? "套用縣市長風格後的機率" : "停班停課機率（法規標準）"}</small></div>
      ${styleBlock(c)}
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

  // 縣市長風格：依歷年公告估計在相同風雨下的放假傾向
  function styleBlock(c) {
    if (c.p_style == null) return "";
    const st = c.style;
    const rec = st ? `任內全台有停班停課公告的 ${st.days} 天中，${c.name}放假 ${st.dayoffs} 天；風雨達標時放假 ${st.dayoff_when_met}/${st.met_criteria}，未達標仍放假 ${st.dayoff_when_not_met}/${st.not_met}` : "任內紀錄不足，採縣市過去的平均傾向";
    const odds = st ? `相同風雨下放假勝算約為平均的 ${st.odds_ratio >= 1 ? st.odds_ratio.toFixed(1) : st.odds_ratio.toFixed(2)} 倍` : "";
    return `<div class="mayor-box">
      <div><b>${esc_(c.mayor)}</b>${st ? `<span class="style-tag">${esc_(st.label)}</span>` : ""}</div>
      <div class="muted">${esc_(rec)}${odds ? "。" + esc_(odds) : ""}</div>
      <div class="cmp"><span>法規標準 <b>${pc(c.probability)}</b></span><span>套用縣市長風格 <b>${pc(c.p_style)}</b></span></div>
    </div>`;
  }

  // 歷年回測成績
  function modelNote() {
    const m = ev.style_model;
    if (!m) return "";
    const f = (k) => `${(m.cv[k].accuracy * 100).toFixed(0)}%`;
    return `<p>🎲 縣市長風格：以預報達標機率為基礎，加上兩項由人事行政總處 ${esc_(m.period[0])} ~ ${esc_(m.period[1])}
      共 ${m.events} 次事件、${m.samples} 筆縣市公告學到的修正：「大颱風一起放」（全台多數縣市達標時，鄰近縣市常跟著放）
      與各縣市長的放假傾向。歷年回測判斷正確率：法規標準 ${f("rule")}、縣市長風格 ${f("style")}（歷史資料只有事後的實際風雨，
      無法完整重現當時的預報，兩者差異不大）。屬娛樂性質，實際以各縣市政府公告為準。</p>`;
  }

  // 公告後：拿公告前最後一次預測對比實際結果
  function renderReview() {
    const el = document.getElementById("evd-review");
    const rv = (ev.review || []).filter((r) => r.counties.length);
    if (!rv.length) { el.innerHTML = ""; return; }
    el.innerHTML = rv.map((r) => {
      const hit = (k) => r.counties.filter((c) => c[k] != null && ((c[k] >= 0.5) === c.off)).length;
      const n = r.counties.length, hasStyle = r.counties.some((c) => c.style != null);
      const rows = [...r.counties].sort((a, b) => b.rule - a.rule).map((c) => {
        const mark = (p) => (p == null ? "" : (p >= 0.5) === c.off ? "<i class=ok>✓</i>" : "<i class=ng>✗</i>");
        return `<tr><td>${esc_(c.name)}</td><td>${pc(c.rule)} ${mark(c.rule)}</td>${hasStyle ? `<td>${c.style == null ? "—" : pc(c.style) + " " + mark(c.style)}</td>` : ""}
          <td><span class="act ${c.off ? "off" : ""}">${STATUS[c.actual] || c.actual}</span></td></tr>`;
      }).join("");
      return `<details class="evd-review" open><summary><b>${dayName(r.date)} 實際公告對比</b>
        <span>預測時間 ${esc_(r.made_at.replace("T", " ").slice(5, 16))} · 法規標準猜中 ${hit("rule")}/${n}${hasStyle ? ` · 縣市長風格猜中 ${hit("style")}/${n}` : ""}</span></summary>
        <div class="table-wrap"><table><tr><th>縣市</th><th>法規標準</th>${hasStyle ? "<th>縣市長風格</th>" : ""}<th>實際</th></tr>${rows}</table></div></details>`;
    }).join("");
  }

  function select(id) {
    selected = id;
    renderList();
    renderDetail();
  }

  return { load };
})();
TaiwanEvent.load();

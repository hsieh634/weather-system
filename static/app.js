"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 1, unit = "") => (v === null || v === undefined || Number.isNaN(v) ? "—" : Number(v).toFixed(d) + unit);
const pct = (v, d = 0) => (v === null || v === undefined ? "—" : (v * 100).toFixed(d) + "%");
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const ICON_CODE = { sun: 0, partly: 2, cloud: 3, fog: 45, showers: 80, rain: 63, heavy_rain: 65, thunder: 95, snow: 73 };
const DIRS = ["北", "東北", "東", "東南", "南", "西南", "西", "西北"];
const WEEK = ["日", "一", "二", "三", "四", "五", "六"];
const QUICK = [
  ["台北", 25.0531, 121.5264, "Asia/Taipei", "TW", "臺北市"], ["台中", 24.1469, 120.6839, "Asia/Taipei", "TW", "臺中市"],
  ["高雄", 22.6163, 120.3133, "Asia/Taipei", "TW", "高雄市"], ["花蓮", 23.9769, 121.6044, "Asia/Taipei", "TW", "花蓮縣"],
  ["東京", 35.6895, 139.6917, "Asia/Tokyo", "JP", ""], ["香港", 22.2783, 114.1747, "Asia/Hong_Kong", "HK", ""],
  ["紐約", 40.7143, -74.006, "America/New_York", "US", ""], ["奧克拉荷馬市", 35.4676, -97.5164, "America/Chicago", "US", ""],
];
const charts = {};
let data = null;
let selectedDay = 0;
let meteoMode = "temp";

// ---------------------------------------------------------------- 搜尋
const q = $("#q"), suggest = $("#suggest");
let suggestions = [], suggestIdx = -1, timer = null;

q.addEventListener("input", () => {
  clearTimeout(timer);
  const v = q.value.trim();
  if (v.length < 1) return hideSuggest();
  timer = setTimeout(() => lookup(v), 300);
});
q.addEventListener("keydown", (e) => {
  if (suggest.hidden) return;
  if (e.key === "ArrowDown") { suggestIdx = Math.min(suggestIdx + 1, suggestions.length - 1); renderSuggest(); e.preventDefault(); }
  if (e.key === "ArrowUp") { suggestIdx = Math.max(suggestIdx - 1, 0); renderSuggest(); e.preventDefault(); }
  if (e.key === "Escape") hideSuggest();
});
$("#search").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!suggest.hidden && suggestions[suggestIdx]) return choose(suggestions[suggestIdx]);
  const v = q.value.trim();
  if (!v) return;
  await lookup(v);
  if (suggestions.length) choose(suggestions[0]);
  else showError(`找不到「${v}」，請換個名稱或直接輸入緯度,經度`);
});
document.addEventListener("click", (e) => { if (!e.target.closest(".search")) hideSuggest(); });

async function lookup(v) {
  try {
    const r = await fetch(`/api/geocode?q=${encodeURIComponent(v)}`);
    suggestions = r.ok ? await r.json() : [];
  } catch { suggestions = []; }
  suggestIdx = suggestions.length ? 0 : -1;
  renderSuggest();
}
function renderSuggest() {
  if (!suggestions.length) return hideSuggest();
  suggest.innerHTML = suggestions.map((p, i) =>
    `<li data-i="${i}" class="${i === suggestIdx ? "on" : ""}">${esc(p.name)}<small>${esc([p.admin1, p.country].filter(Boolean).join("，"))}</small></li>`).join("");
  suggest.hidden = false;
  suggest.querySelectorAll("li").forEach((li) => li.addEventListener("click", () => choose(suggestions[+li.dataset.i])));
}
function hideSuggest() { suggest.hidden = true; }
function choose(p) {
  hideSuggest();
  q.value = p.name;
  load({ lat: p.latitude, lon: p.longitude, name: [p.name, p.admin1, p.country].filter(Boolean).join("，"),
         tz: p.timezone || "auto", cc: p.country_code || "", admin: [p.admin1, p.admin2].filter(Boolean).join("|") });
}

$("#locate").addEventListener("click", () => {
  if (!navigator.geolocation) return showError("這個瀏覽器不支援定位");
  navigator.geolocation.getCurrentPosition(
    (pos) => load({ lat: +pos.coords.latitude.toFixed(4), lon: +pos.coords.longitude.toFixed(4), name: "我的位置", tz: "auto" }),
    () => showError("無法取得位置，請確認瀏覽器已允許定位"));
});

$("#quick").innerHTML = QUICK.map((c, i) => `<button data-i="${i}">${c[0]}</button>`).join("");
$("#quick").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
  const [name, lat, lon, tz, cc, admin] = QUICK[+b.dataset.i];
  q.value = name;
  load({ lat, lon, name, tz, cc, admin });
}));

// ---------------------------------------------------------------- 載入
async function load(p) {
  $("#error").hidden = true;
  $("#welcome").hidden = true;
  $("#loading").hidden = false;
  $("#result").classList.add("stale");
  $("#loading-title").textContent = `正在產生「${p.name}」的預報…`;
  const msgs = ["下載 7 個全球模式的最新預報…", "第一次查詢此地點：下載約兩年的歷史預報與實際觀測…",
    "訓練在地化機器學習修正模型…", "用沒看過的資料驗證第 1～7 天的準確率…", "分析雷雨、冰雹、龍捲風等劇烈天氣環境…", "檢查全球颱風與聖嬰現況…"];
  let i = 0;
  $("#loading-msg").textContent = msgs[0];
  const tick = setInterval(() => { i = Math.min(i + 1, msgs.length - 1); $("#loading-msg").textContent = msgs[i]; }, 9000);
  try {
    const params = new URLSearchParams({ lat: p.lat, lon: p.lon, name: p.name, tz: p.tz || "auto", cc: p.cc || "", admin: p.admin || "" });
    const r = await fetch(`/api/forecast?${params}`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `伺服器錯誤 ${r.status}`);
    data = await r.json();
    selectedDay = 0;
    render();
    try { localStorage.setItem("wx:last", JSON.stringify(p)); } catch {}
  } catch (e) {
    showError(e.message);
    if (!data) $("#welcome").hidden = false;
  } finally {
    clearInterval(tick);
    $("#loading").hidden = true;
    $("#result").classList.remove("stale");
  }
}
function showError(msg) { const el = $("#error"); el.textContent = "⚠ " + msg; el.hidden = false; }

// ---------------------------------------------------------------- 繪製
function render() {
  $("#result").hidden = false;
  WeatherMap.show(data.location);
  renderBanner(); renderNow(); renderAccuracy(); renderNext24(); renderDays(); renderMeteogram();
  renderHazards(); renderAlerts(); renderTropical(); renderEnso(); renderMethod(); renderVerify();
}

function dayLabel(d) {
  const dt = new Date(d.date + "T00:00");
  const name = d.lead === 0 ? "今天" : d.lead === 1 ? "明天" : d.lead === 2 ? "後天" : "週" + WEEK[dt.getDay()];
  return { name, date: `${dt.getMonth() + 1}/${dt.getDate()}` };
}

function renderBanner() {
  const items = [];
  data.alerts.alerts.forEach((a) => items.push({ level: a.level, title: `官方警報：${a.event}`, sub: a.headline }));
  data.tropical.storms.filter((s) => s.threat_level >= 2).forEach((s) =>
    items.push({ level: s.threat_level, title: `${s.kind} ${s.name}：距離約 ${s.distance_km} km`, sub: `${s.category}。${s.threat}` }));
  data.daily.slice(0, 3).forEach((d) => d.hazards.filter((h) => h.level >= 2).forEach((h) =>
    items.push({ level: h.level, title: `${dayLabel(d).name}：${h.title}（${h.level_name}）`, sub: h.detail })));
  items.sort((a, b) => b.level - a.level);
  $("#banner").innerHTML = items.slice(0, 5).map((it) =>
    `<div class="banner l${it.level}"><span>${it.level >= 3 ? "🚨" : "⚠️"}</span><div><b>${esc(it.title)}</b>${esc(it.sub)}</div></div>`).join("");
}

function renderNow() {
  const c = data.current, d0 = data.daily[0], loc = data.location;
  $("#now").innerHTML = `
    <div class="place">${esc(loc.name)}</div>
    <div class="coords">${fmt(loc.latitude, 3)}, ${fmt(loc.longitude, 3)} · 海拔 ${fmt(loc.elevation, 0)} m · ${esc(loc.timezone)} · 更新 ${esc(data.generated_at.replace("T", " ").slice(0, 16))}</div>
    <div class="big"><span class="icon">${wxSvg(c.weather_code ?? ICON_CODE[d0.icon], c.is_day !== 0, 72)}</span>
      <span class="t">${fmt(c.temperature, 1)}°</span>
      <div><b>${esc(d0.text)}</b><br><span class="muted">今日 ${fmt(d0.tmin, 0)}° ~ ${fmt(d0.tmax, 0)}°，降雨機率 ${pct(d0.rain_prob)}</span></div></div>
    <div class="stats">
      <div class="stat"><span>相對濕度</span><b>${fmt(c.humidity, 0, "%")}</b></div>
      <div class="stat"><span>風速／陣風</span><b>${fmt(c.wind, 0)} / ${fmt(c.gust, 0)} km/h</b></div>
      <div class="stat"><span>海平面氣壓</span><b>${fmt(c.pressure, 0, " hPa")}</b></div>
      <div class="stat"><span>本小時雨量</span><b>${fmt(c.precipitation, 1, " mm")}</b></div>
      <div class="stat"><span>模式間溫度分歧</span><b>±${fmt(c.temp_spread, 1)}°C</b></div>
    </div>`;
}

function renderAccuracy() {
  const v = data.verification, bl = v.by_lead;
  const leads = Object.keys(bl).sort((a, b) => a - b);
  const truth = v.station ? `以 ${esc(v.station.name)} 氣象站（${v.station.distance_km} km）實測為準` : "以 ERA5 再分析為準（附近無公開氣象站）";
  $("#acc-sub").innerHTML = `考卷期 ${esc(v.test_period[0])} ~ ${esc(v.test_period[1])}，${truth}。明天最高溫 ±2°C 命中率 <b>${pct(bl["1"].tmax_within_2)}</b>、降雨判斷 <b>${pct(bl["1"].rain_accuracy)}</b>`;
  drawChart("acc", "#acc-chart", {
    type: "line",
    data: {
      labels: leads.map((l) => `第${l}天`),
      datasets: [
        { label: "最高溫 ±2°C", data: leads.map((l) => bl[l].tmax_within_2 * 100), borderColor: css("--warm"), backgroundColor: css("--warm") },
        { label: "最低溫 ±2°C", data: leads.map((l) => bl[l].tmin_within_2 * 100), borderColor: css("--cool"), backgroundColor: css("--cool") },
        { label: "降雨判斷", data: leads.map((l) => bl[l].rain_accuracy * 100), borderColor: css("--ok"), backgroundColor: css("--ok") },
      ],
    },
    options: baseOptions({ y: { min: 40, max: 100, ticks: { callback: (x) => x + "%" } } }, (ctx) => `${ctx.dataset.label}：${ctx.parsed.y.toFixed(0)}%`),
  });
}

// ---------------------------------------------------------------- 天氣圖示（SVG）
document.body.insertAdjacentHTML("afterbegin", `<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
  <linearGradient id="wx-sun" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffd84d"/><stop offset="1" stop-color="#ff9f1a"/></linearGradient>
  <linearGradient id="wx-cloud" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#dfe5ff"/><stop offset="1" stop-color="#8fa2f2"/></linearGradient>
  <linearGradient id="wx-dark" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#b9c1dd"/><stop offset="1" stop-color="#6b76a3"/></linearGradient>
  <linearGradient id="wx-moon" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fff1b0"/><stop offset="1" stop-color="#f2c94c"/></linearGradient>
</defs></svg>`);

function wxSvg(code, isDay = true, size = 48, attrs = "") {
  code = code ?? 3;
  const sun = (cx, cy, r) => {
    let rays = "";
    for (let k = 0; k < 8; k++) {
      const a = (k * Math.PI) / 4, r1 = r + 3, r2 = r + 7;
      rays += `<line x1="${cx + r1 * Math.cos(a)}" y1="${cy + r1 * Math.sin(a)}" x2="${cx + r2 * Math.cos(a)}" y2="${cy + r2 * Math.sin(a)}"/>`;
    }
    return `<g stroke="#ffb020" stroke-width="2.6" stroke-linecap="round">${rays}</g><circle cx="${cx}" cy="${cy}" r="${r}" fill="url(#wx-sun)"/>`;
  };
  const moon = (cx, cy, r) => `<path d="M${cx + r * 0.3} ${cy - r} a${r} ${r} 0 1 0 ${r * 0.7} ${r * 1.65} a${r * 0.8} ${r * 0.8} 0 1 1 ${-r * 0.7} ${-r * 1.65}z" fill="url(#wx-moon)"/>`;
  const orb = (cx, cy, r) => (isDay ? sun(cx, cy, r) : moon(cx, cy, r));
  const cloud = (dx = 0, dy = 0, s = 1, fill = "url(#wx-cloud)") =>
    `<path transform="translate(${dx} ${dy}) scale(${s})" d="M16 48h30a10 10 0 0 0 1-20 14 14 0 0 0-27-4 11 11 0 0 0-4 24z" fill="${fill}"/>`;
  const drops = (n) => Array.from({ length: n }, (_, k) => {
    const x = 20 + k * (24 / Math.max(n - 1, 1));
    return `<path d="M${x} 52 l-2.5 6" stroke="#4f6df5" stroke-width="3" stroke-linecap="round"/>`;
  }).join("");
  const flakes = (n) => Array.from({ length: n }, (_, k) => `<text x="${18 + k * 11}" y="61" font-size="11" fill="#7fb6ff">✱</text>`).join("");
  let body;
  if (code === 0) body = orb(32, 32, 12);
  else if (code === 1) body = orb(26, 24, 11) + cloud(10, 10, 0.75);
  else if (code === 2) body = orb(24, 22, 11) + cloud(4, 4, 0.9);
  else if (code === 3) body = cloud(0, 0, 1);
  else if (code === 45 || code === 48) body = cloud(0, -4, 1) + `<g stroke="#9aa6c4" stroke-width="3" stroke-linecap="round"><line x1="12" y1="50" x2="52" y2="50"/><line x1="16" y1="57" x2="48" y2="57"/></g>`;
  else if (code >= 51 && code <= 57) body = cloud(0, -4, 1) + drops(2);
  else if (code === 61 || code === 63 || code === 66) body = cloud(0, -4, 1) + drops(3);
  else if (code === 65 || code === 67) body = cloud(0, -4, 1, "url(#wx-dark)") + drops(4);
  else if (code >= 80 && code <= 82) body = (isDay ? sun(22, 18, 9) : moon(22, 18, 9)) + cloud(2, -2, 0.95) + drops(code === 82 ? 4 : 3);
  else if ((code >= 71 && code <= 77) || code === 85 || code === 86) body = cloud(0, -4, 1) + flakes(3);
  else if (code >= 95) body = cloud(0, -6, 1, "url(#wx-dark)") + `<path d="M33 40l-8 12h6l-3 10 10-14h-6l4-8z" fill="#ffc21a"/>` + drops(2);
  else body = cloud(0, 0, 1);
  return `<svg ${attrs} width="${size}" height="${size}" viewBox="0 0 64 64" role="img" aria-hidden="true">${body}</svg>`;
}

// 依溫度上色（溫度色帶）
const TEMP_STOPS = [[-25, [130, 90, 200]], [-10, [110, 150, 235]], [0, [150, 205, 245]], [10, [165, 225, 200]],
  [18, [230, 238, 170]], [23, [250, 220, 175]], [27, [244, 196, 186]], [31, [236, 176, 196]], [36, [222, 140, 190]], [42, [190, 100, 170]]];
function tempColor(t) {
  if (t === null || t === undefined) return "transparent";
  const s = TEMP_STOPS;
  if (t <= s[0][0]) return `rgb(${s[0][1]})`;
  for (let k = 1; k < s.length; k++) {
    if (t <= s[k][0]) {
      const [t0, c0] = s[k - 1], [t1, c1] = s[k], f = (t - t0) / (t1 - t0);
      return `rgb(${c0.map((v, j) => Math.round(v + (c1[j] - v) * f)).join(",")})`;
    }
  }
  return `rgb(${s[s.length - 1][1]})`;
}
function gustColor(g) {
  if (g === null || g === undefined || g < 40) return null;
  return g >= 118 ? "#7b2cbf" : g >= 90 ? "#c0396b" : g >= 62 ? "#e8590c" : "#f2b705";
}
const dirName = (d) => (d === null || d === undefined ? "" : DIRS[Math.round(d / 45) % 8]);
const mm = (p) => (p === null || p === undefined ? "—" : p < 0.05 ? "0" : p < 10 ? p.toFixed(1) : Math.round(p));

function hourCol(i, cls = "") {
  const h = data.hourly;
  const hr = +h.time[i].slice(11, 13);  // 地點當地時間（不受瀏覽器時區影響）
  const isDay = h.is_day ? h.is_day[i] !== 0 : hr >= 6 && hr < 18;
  const p = h.precipitation[i] ?? 0, pp = h.precip_prob ? h.precip_prob[i] : null;
  const g = h.gust[i], gc = gustColor(g), dir = h.wind_dir[i];
  return `<div class="hcol ${cls}">
    <div class="tm">${String(hr).padStart(2, "0")}:00</div>
    <div class="ic">${wxSvg(h.weather_code ? h.weather_code[i] : null, isDay, 50)}</div>
    <div class="tg" data-t="${h.temperature[i] ?? ""}"></div>
    <div class="pr ${p >= 0.1 ? "wet" : ""}">${p >= 0.1 ? "💧" : ""}${mm(p)} mm</div>
    <div class="pp ${pp >= 0.3 ? "wet" : ""}">${pct(pp)}</div>
    <div class="wd"><span class="warr" title="風向：${dirName(dir)}風"><i style="transform:rotate(${dir ?? 0}deg)">↓</i></span>${dirName(dir)}風</div>
    <div class="ws">${fmt(h.wind[i], 0)} km/h</div>
    <div class="gv">${gc ? `<i class="gdot" style="background:${gc}"></i>` : ""}陣風 ${fmt(g, 0)}</div>
  </div>`;
}

function renderNext24() {
  const h = data.hourly;
  let start = h.time.findIndex((t) => t >= data.now_local);
  if (start < 0) start = 0;
  const cols = [];
  for (let i = start; i < Math.min(start + 24, h.time.length); i++) cols.push(hourCol(i, i === start ? "now" : ""));
  $("#next24").innerHTML = cols.join("");
  drawCurve($("#next24"));
}

function renderDays() {
  const days = data.daily;
  $("#days").innerHTML = days.map((d, i) => {
    const lb = dayLabel(d);
    const top = d.hazards.length ? Math.max(...d.hazards.map((x) => x.level)) : 0;
    return `<button class="dcol ${i === selectedDay ? "on" : ""}" data-i="${i}" title="${esc(d.text)}">
      ${top ? `<span class="hz l${top}" title="${esc(d.hazards.map((x) => x.title).join("、"))}"></span>` : ""}
      <div class="wk">${lb.name.replace("週", "")}</div>
      <div class="ic">${wxSvg(ICON_CODE[d.icon], true, 40)}</div>
      <div class="mx">${fmt(d.tmax, 0)} °C</div></button>`;
  }).join("");
  $("#days").querySelectorAll(".dcol").forEach((b) => b.addEventListener("click", () => selectDay(+b.dataset.i)));

  const sel = $("#day-select");
  sel.innerHTML = days.map((d, i) => `<option value="${i}" ${i === selectedDay ? "selected" : ""}>${d.date}</option>`).join("");
  sel.onchange = () => selectDay(+sel.value);
  $("#day-prev").disabled = selectedDay === 0;
  $("#day-next").disabled = selectedDay === days.length - 1;
  $("#day-prev").onclick = () => selectDay(selectedDay - 1);
  $("#day-next").onclick = () => selectDay(selectedDay + 1);

  // 選定那一天的每 3 小時
  const date = days[selectedDay].date, h = data.hourly;
  const cols = [];
  [2, 5, 8, 11, 14, 17, 20, 23].forEach((hr) => {
    const i = h.time.indexOf(`${date}T${String(hr).padStart(2, "0")}:00:00`);
    if (i >= 0) cols.push(hourCol(i));
  });
  $("#day3h").innerHTML = cols.join("") || `<div class="empty" style="padding:12px">這一天沒有逐時資料</div>`;
  drawCurve($("#day3h"));
  renderDayDetail();
}
function selectDay(i) {
  selectedDay = Math.max(0, Math.min(i, data.daily.length - 1));
  renderDays();
}

function renderDayDetail() {
  const d = data.daily[selectedDay], lb = dayLabel(d), names = data.model_names;
  const a = d.expected_accuracy;
  const models = Object.keys(d.per_model.tmax);
  const rows = models.map((m) => `<tr><td>${esc(names[m] || m)}</td><td class="num">${fmt(d.per_model.tmax[m], 1, "°")}</td><td class="num">${fmt(d.per_model.precip[m], 1)}</td></tr>`).join("");
  $("#day-detail").innerHTML = `<div class="dd">
    <div><h3>${lb.name} ${lb.date}：${esc(d.text)}</h3><table>
      <tr><th>最高溫（80% 區間）</th><td class="num">${fmt(d.tmax, 1)}°C（${fmt(d.tmax_range[0], 0)}~${fmt(d.tmax_range[1], 0)}）</td></tr>
      <tr><th>最低溫（80% 區間）</th><td class="num">${fmt(d.tmin, 1)}°C（${fmt(d.tmin_range[0], 0)}~${fmt(d.tmin_range[1], 0)}）</td></tr>
      <tr><th>降雨機率（系統／系集）</th><td class="num">${pct(d.rain_prob)} / ${pct(d.rain_prob_ensemble)}</td></tr>
      <tr><th>預估雨量</th><td class="num">${fmt(d.precip_mm, 1)} mm（約 ${fmt(d.precip_hours, 0)} 小時有雨）</td></tr>
      <tr><th>≥10 mm／≥80 mm 機率</th><td class="num">${pct(d.p_rain_10mm)} / ${pct(d.p_rain_80mm)}</td></tr>
      <tr><th>降雪</th><td class="num">${fmt(d.snow_cm, 1)} cm（機率 ${pct(d.p_snow)}）</td></tr>
      <tr><th>最大風速／陣風</th><td class="num">${fmt(d.wind_max, 0)} / ${fmt(d.gust_max, 0)} km/h（${d.beaufort ?? "—"} 級）</td></tr>
      <tr><th>平均濕度／最低氣壓</th><td class="num">${fmt(d.humidity, 0, "%")} / ${fmt(d.pressure_min, 0)} hPa</td></tr>
    </table></div>
    <div><h3>這一天的預估準確率</h3>${a ? `<table>
      <tr><th>最高溫 ±2°C 命中率</th><td class="num">${pct(a.tmax_within_2)}</td></tr>
      <tr><th>最低溫 ±2°C 命中率</th><td class="num">${pct(a.tmin_within_2)}</td></tr>
      <tr><th>最高溫平均誤差</th><td class="num">${fmt(a.tmax_mae, 2)}°C</td></tr>
      <tr><th>降雨判斷正確率</th><td class="num">${pct(a.rain_accuracy)}</td></tr>
      <tr><th>下雨時抓到的比例</th><td class="num">${pct(a.rain_pod)}</td></tr>
      <tr><th>報有雨卻沒下的比例</th><td class="num">${pct(a.rain_far)}</td></tr></table>`
      : `<p class="muted">超過 7 天的預報未納入驗證，混沌效應使準確度明顯下降，請當作趨勢參考。各模式分歧：±${fmt(d.model_spread_tmax, 1)}°C</p>`}
      <h3 style="margin-top:12px">劇烈天氣</h3>
      ${d.hazards.length ? d.hazards.map(hazardRow).join("") : `<div class="empty">無明顯劇烈天氣訊號</div>`}
    </div>
    <div><h3>各模式原始預報</h3><div class="table-wrap"><table><tr><th>模式</th><th class="num">最高溫</th><th class="num">雨量 mm</th></tr>${rows}</table></div></div>
  </div>`;
}

function hazardRow(h, prefix = "") {
  return `<div class="hz-item"><span class="tag l${h.level}">${esc(h.level_name)}</span><div><b>${esc(prefix)}${esc(h.title)}</b><div class="sub">${esc(h.detail)}</div></div></div>`;
}

// ---------------------------------------------------------------- 逐時溫度曲線
// 溫度 → 線條色（冷藍到暖紅的連續色相，比表格底色更飽和，線條才看得清楚）
function tempStroke(t) {
  const h = Math.max(0, Math.min(250, 250 - ((t + 10) / 48) * 250));
  return `hsl(${h.toFixed(0)}, 72%, 46%)`;
}
function drawCurve(strip) {
  strip.querySelector(".tcurve")?.remove();
  const cells = [...strip.querySelectorAll(".tg")];
  if (cells.length < 2) return;
  const temps = cells.map((c) => (c.dataset.t === "" ? null : +c.dataset.t));
  const v = temps.filter((x) => x !== null);
  const lo = Math.min(...v), span = Math.max(Math.max(...v) - lo, 3);
  const top = cells[0].offsetTop, H = cells[0].offsetHeight, W = strip.scrollWidth;
  const xs = cells.map((c) => c.offsetLeft + c.offsetWidth / 2);
  const y = (t) => 24 + (1 - (t - lo) / span) * (H - 34);
  const id = "tc" + Math.random().toString(36).slice(2, 8);
  const stops = temps.map((t, k) => (t === null ? "" : `<stop offset="${((xs[k] / W) * 100).toFixed(2)}%" stop-color="${tempStroke(t)}"/>`)).join("");
  const pts = temps.map((t, k) => (t === null ? null : [xs[k], y(t)])).filter(Boolean);
  const line = pts.map((p) => p.join(",")).join(" ");
  const area = `M${pts[0][0]},${H} L${line.replace(/ /g, " L")} L${pts[pts.length - 1][0]},${H} Z`;
  const dots = temps.map((t, k) => (t === null ? "" :
    `<circle cx="${xs[k]}" cy="${y(t)}" r="4" fill="${tempStroke(t)}" stroke="var(--card)" stroke-width="2"/>
     <text x="${xs[k]}" y="${y(t) - 10}" text-anchor="middle" font-size="13" font-weight="700" fill="var(--text)">${Math.round(t)}°</text>`)).join("");
  strip.insertAdjacentHTML("beforeend", `<svg class="tcurve" width="${W}" height="${H}" style="top:${top}px" aria-hidden="true">
    <defs><linearGradient id="${id}" gradientUnits="userSpaceOnUse" x1="0" x2="${W}" y1="0" y2="0">${stops}</linearGradient></defs>
    <path d="${area}" fill="url(#${id})" opacity="0.13"/>
    <polyline points="${line}" fill="none" stroke="url(#${id})" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>${dots}</svg>`);
}

// ---------------------------------------------------------------- 每日趨勢圖
function renderMeteogram() {
  const el = $("#meteogram"), days = data.daily, n = days.length;
  const left = 44, right = 14, top = 26, plotH = 250;
  const colW = Math.max(36, ((el.clientWidth || 720) - left - right) / n);
  const W = Math.round(left + right + colW * n), H = top + plotH + 84;
  const x = (i) => left + colW * (i + 0.5);
  const muted = css("--muted"), grid = css("--line"), accent = css("--accent"), text = css("--text");
  let lo, hi, step, unit, body = "", defs = "";
  const y = (v) => top + plotH - ((v - lo) / (hi - lo)) * plotH;
  const nice = (a, b, minStep, pad) => {
    step = minStep;
    while ((b - a + 2 * pad) / step > 6) step *= 2;
    lo = Math.floor((a - pad) / step) * step;
    hi = Math.ceil((b + pad) / step) * step;
    if (hi === lo) hi = lo + step;
  };

  if (meteoMode === "temp") {
    // 每天一根「溫度區間柱」：下緣最低溫、上緣最高溫，顏色隨溫度漸層
    const tx = days.map((d) => d.tmax), tn = days.map((d) => d.tmin);
    nice(Math.min(...tn), Math.max(...tx), 2, 2);
    unit = "°C";
    const bw = Math.min(16, colW * 0.36);
    days.forEach((d, i) => {
      if (d.tmax === null || d.tmin === null) return;
      const id = `tb${i}`;
      defs += `<linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${tempStroke(d.tmax)}"/><stop offset="1" stop-color="${tempStroke(d.tmin)}"/></linearGradient>`;
      const y1 = y(d.tmax), y2 = y(d.tmin);
      body += `<rect x="${x(i) - bw / 2}" y="${y1}" width="${bw}" height="${Math.max(4, y2 - y1)}" rx="${bw / 2}" fill="url(#${id})"/>
        <text x="${x(i)}" y="${y1 - 8}" text-anchor="middle" font-size="13" font-weight="700" fill="${text}">${Math.round(d.tmax)}°</text>
        <text x="${x(i)}" y="${y2 + 17}" text-anchor="middle" font-size="12" fill="${muted}">${Math.round(d.tmin)}°</text>`;
    });
  } else if (meteoMode === "precip") {
    // 雨量長條 + 降雨機率折線（右側刻度 0~100%）
    const p = days.map((d) => d.precip_mm ?? 0);
    lo = 0; step = 1;
    const top_ = Math.max(4, ...p) * 1.25;
    while (top_ / step > 6) step = step < 5 ? step + 1 : step * 2;
    hi = Math.ceil(top_ / step) * step;
    unit = "mm";
    const bw = Math.min(24, colW * 0.5);
    body = p.map((v, i) => `<rect x="${x(i) - bw / 2}" y="${y(v)}" width="${bw}" height="${top + plotH - y(v)}" rx="3" fill="${css("--rain")}" opacity="${v < 0.1 ? 0 : 0.85}"/>
      ${v >= 0.1 ? `<text x="${x(i)}" y="${y(v) - 6}" text-anchor="middle" font-size="12" font-weight="700" fill="${css("--rain")}">${mm(v)}</text>` : ""}`).join("");
    const py = (q) => top + plotH - q * plotH;
    const pts = days.map((d, i) => (d.rain_prob === null ? null : `${x(i)},${py(d.rain_prob)}`)).filter(Boolean).join(" ");
    body += `<polyline points="${pts}" fill="none" stroke="${muted}" stroke-width="1.8" stroke-dasharray="4 4"/>`
      + days.map((d, i) => (d.rain_prob === null ? "" : `<circle cx="${x(i)}" cy="${py(d.rain_prob)}" r="3.5" fill="var(--card)" stroke="${muted}" stroke-width="1.8"><title>降雨機率 ${pct(d.rain_prob)}</title></circle>`)).join("")
      + [0, 0.5, 1].map((q) => `<text x="${W - right + 2}" y="${py(q) + 4}" font-size="10" fill="${muted}">${q * 100}%</text>`).join("");
  } else {
    // 陣風折線 + 蒲福 8 / 10 / 12 級門檻線（10 級為停班停課參考標準）
    const g = days.map((d) => d.gust_max);
    const gmax = Math.max(...g.filter((v) => v !== null));
    nice(0, Math.max(gmax, gmax > 50 ? 95 : gmax), 10, 6);
    lo = 0;
    unit = "km/h";
    [[62, "8 級 強風"], [89, "10 級 停班課門檻"], [118, "12 級 颱風級"]].forEach(([v, label]) => {
      if (v > hi) return;
      body += `<line x1="${left}" x2="${W - right}" y1="${y(v)}" y2="${y(v)}" stroke="${v >= 89 ? css("--lv3") : css("--lv2")}" stroke-dasharray="6 4" stroke-width="1.3"/>
        <text x="${W - right - 4}" y="${y(v) - 5}" text-anchor="end" font-size="11" font-weight="600" fill="${v >= 89 ? css("--lv3") : css("--lv2")}">${label}</text>`;
    });
    const pts = g.map((v, i) => (v === null ? null : [x(i), y(v)])).filter(Boolean);
    defs += `<linearGradient id="garea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${accent}" stop-opacity=".35"/><stop offset="1" stop-color="${accent}" stop-opacity="0"/></linearGradient>`;
    body += `<path d="M${pts[0][0]},${top + plotH} L${pts.map((p) => p.join(",")).join(" L")} L${pts[pts.length - 1][0]},${top + plotH} Z" fill="url(#garea)"/>
      <polyline points="${pts.map((p) => p.join(",")).join(" ")}" fill="none" stroke="${accent}" stroke-width="2.5" stroke-linejoin="round"/>`
      + g.map((v, i) => (v === null ? "" : `<circle cx="${x(i)}" cy="${y(v)}" r="4" fill="${gustColor(v) || accent}" stroke="var(--card)" stroke-width="2"/>
        <text x="${x(i)}" y="${y(v) - 10}" text-anchor="middle" font-size="12" font-weight="700" fill="${text}">${Math.round(v)}</text>`)).join("");
  }

  let axes = "";
  for (let v = lo; v <= hi + 1e-9; v += step) {
    axes += `<line x1="${left}" x2="${W - right}" y1="${y(v)}" y2="${y(v)}" stroke="${grid}" stroke-dasharray="${v === lo ? "" : "2 4"}"/>
      <text x="${left - 8}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="${muted}">${+v.toFixed(1)}</text>`;
  }
  const cols = days.map((d, i) => {
    const dt = new Date(d.date + "T00:00"), wk = d.lead === 0 ? "今天" : "週" + WEEK[dt.getDay()];
    const sel = i === selectedDay;
    return `<g class="mcol" data-i="${i}" style="cursor:pointer">
      <rect x="${x(i) - colW / 2 + 1}" y="${top - 6}" width="${colW - 2}" height="${plotH + 86}" rx="8" fill="${sel ? css("--accent-soft") : "transparent"}"/>
      ${wxSvg(ICON_CODE[d.icon], true, 28, `x="${x(i) - 14}" y="${top + plotH + 6}"`)}
      <text x="${x(i)}" y="${top + plotH + 52}" text-anchor="middle" font-size="12" font-weight="${sel ? 700 : 500}" fill="${sel ? accent : text}">${wk}</text>
      <text x="${x(i)}" y="${top + plotH + 68}" text-anchor="middle" font-size="11" fill="${muted}">${dt.getMonth() + 1}/${dt.getDate()}</text></g>`;
  }).join("");

  el.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="每日${{ temp: "溫度範圍", precip: "雨量與降雨機率", gust: "最大陣風" }[meteoMode]}趨勢圖">
    <defs>${defs}</defs>${cols}${axes}
    <text x="${left - 8}" y="${top - 12}" text-anchor="end" font-size="11" fill="${muted}">${unit}</text>
    ${body}</svg>`;
  el.querySelectorAll(".mcol").forEach((gEl) => gEl.addEventListener("click", () => {
    selectDay(+gEl.dataset.i);
    renderMeteogram();
  }));
}
$("#meteo-tabs").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
  meteoMode = b.dataset.k;
  $("#meteo-tabs").querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
  renderMeteogram();
}));
let resizeTimer;
addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (data) { renderMeteogram(); drawCurve($("#next24")); drawCurve($("#day3h")); } }, 150); });

function renderHazards() {
  const rows = [];
  data.daily.forEach((d) => d.hazards.forEach((h) => rows.push({ d, h })));
  rows.sort((a, b) => b.h.level - a.h.level || a.d.lead - b.d.lead);
  $("#hazards").innerHTML = rows.length
    ? rows.slice(0, 14).map(({ d, h }) => { const lb = dayLabel(d); return hazardRow(h, `${lb.name} ${lb.date}　`); }).join("")
      + `<p class="muted" style="margin-top:10px">龍捲風、冰雹屬於小尺度現象，數值模式無法直接預報，這裡顯示的是「大氣環境是否有利發生」。</p>`
    : `<div class="empty">未來 16 天沒有明顯的劇烈天氣訊號 👍</div>`;
}

function renderAlerts() {
  const a = data.alerts;
  $("#alerts").innerHTML = (a.alerts.length
    ? a.alerts.map((x) => `<div class="hz-item"><span class="tag l${x.level}">${x.level >= 3 ? "警報" : "特報"}</span><div><b>${esc(x.event)}</b>
        <div class="sub">${esc(x.headline)}</div>${x.ends ? `<div class="sub">至 ${esc(x.ends)}</div>` : ""}</div></div>`).join("")
    : `<div class="empty">${a.available ? "目前沒有生效中的官方警報" : ""}</div>`)
    + (a.note ? `<p class="muted">${esc(a.note)}</p>` : "");
}

function renderTropical() {
  const t = data.tropical, loc = data.location;
  $("#tc-list").innerHTML = (t.storms.length ? t.storms.map((s) => `<div class="storm">
      <span class="tag l${s.threat_level}">${s.threat_level ? ["", "注意", "警戒", "危險"][s.threat_level] : "遠方"}</span>
      <span class="name">${esc(s.kind)} ${esc(s.name)}</span> <span class="muted">${esc(s.source)}</span>
      <div class="sub">距離 ${s.distance_km.toLocaleString()} km · ${esc(s.category)} · ${esc(s.wind_note)} ${fmt(s.max_wind_kmh, 0)} km/h
        ${s.pressure_hpa ? ` · ${esc(s.pressure_hpa)} hPa` : ""}${s.movement ? ` · ${esc(s.movement)}` : ""}</div>
      <div class="sub">${esc(s.threat)}${s.link ? ` · <a href="${esc(s.link)}" target="_blank" rel="noopener">官方資料</a>` : ""}</div></div>`).join("")
    : `<div class="empty">目前全球沒有活躍的熱帶氣旋</div>`)
    + (t.errors.length ? `<p class="muted">部分來源暫時無法取得：${esc(t.errors.join("；"))}</p>` : "");
}

function renderEnso() {
  const e = data.enso;
  if (e.error) { $("#enso").innerHTML = `<div class="empty">聖嬰資料暫時無法取得</div>`; return; }
  $("#enso").innerHTML = `<div class="enso-label">${esc(e.label)}</div>
    <div class="muted">ONI（${esc(e.oni.season)} ${e.oni.year}）${e.oni.anomaly > 0 ? "+" : ""}${fmt(e.oni.anomaly, 2)}°C · 趨勢${esc(e.trend)}
      ${e.weekly ? ` · 最新一週 Niño 3.4 ${e.weekly.nino34_anomaly > 0 ? "+" : ""}${fmt(e.weekly.nino34_anomaly, 1)}°C` : ""}</div>
    <ul class="plain">${e.impacts.map((x) => `<li>${esc(x)}</li>`).join("")}</ul><p class="muted">${esc(e.note)}</p>`;
  drawChart("enso", "#enso-chart", {
    type: "bar",
    data: { labels: e.history.map((h) => `${h.season} ${String(h.year).slice(2)}`),
      datasets: [{ label: "ONI 距平 °C", data: e.history.map((h) => h.anomaly),
        backgroundColor: e.history.map((h) => (h.anomaly >= 0.5 ? css("--warm") : h.anomaly <= -0.5 ? css("--cool") : css("--muted"))) }] },
    options: baseOptions({ y: { suggestedMin: -1, suggestedMax: 1 } }, (ctx) => `${ctx.parsed.y.toFixed(2)}°C`, null, false),
  });
}

function renderMethod() {
  const m = data.method, v = data.verification;
  const sel = { best_single: "最佳單一模式", weighted_blend: "多模式加權融合", model_vote: "多模式投票", ml_corrected: "機器學習修正" };
  const vn = { temperature_2m_max: "最高溫", temperature_2m_min: "最低溫", precipitation_sum: "雨量", wind_speed_10m_max: "風速", rain: "降雨機率" };
  const w = Object.entries(m.weights.temperature_2m_max || {}).sort((a, b) => b[1] - a[1]);
  $("#method").innerHTML = `<p class="muted">系統會依照這個地點的歷史表現，自動為每個變數選擇最準的方法：</p>
    <div>${Object.entries(m.selected).map(([k, s]) => `<span class="pill" style="margin:0 6px 6px 0">${vn[k] || k}：${sel[s] || s}</span>`).join("")}</div>
    <h3 style="margin:12px 0 6px;font-size:14px">最高溫各模式權重（依歷史誤差計算）</h3>
    ${w.map(([name, x]) => `<div class="wbar"><span>${esc(name)}</span><div class="b"><i style="width:${(x / w[0][1]) * 100}%"></i></div><span>${pct(x)}</span></div>`).join("")}
    <p class="muted" style="margin-top:10px">訓練資料 ${esc(v.training_period[0])} ~ ${esc(v.training_period[1])}，模型建立於 ${esc(v.trained_at.slice(0, 10))}，每 30 天自動重新訓練。</p>`;
}

function renderVerify() {
  const v = data.verification;
  const order = ["rain", "temperature_2m_max", "temperature_2m_min", "precipitation_sum", "wind_speed_10m_max"];
  const parts = order.filter((k) => v.tables[k]).map((k) => {
    const t = v.tables[k], lead1 = t.leads["1"], methods = Object.keys(lead1).filter((x) => x !== "本系統（自動選用）");
    const isRain = k === "rain", isTemp = k.startsWith("temperature");
    const key = isRain ? "accuracy" : "mae";
    const best = methods.reduce((a, b) => (isRain ? lead1[b][key] > lead1[a][key] : lead1[b][key] < lead1[a][key]) ? b : a);
    const head = isRain ? "<th class='num'>正確率</th><th class='num'>抓到率</th><th class='num'>誤報率</th><th class='num'>Brier</th>"
      : `<th class='num'>平均誤差</th><th class='num'>偏差</th>${isTemp ? "<th class='num'>±1°C</th><th class='num'>±2°C</th>" : ""}`;
    const unit = isTemp ? "°C" : k === "precipitation_sum" ? " mm" : " km/h";
    const rows = methods.map((mname) => { const r = lead1[mname];
      const cells = isRain ? `<td class="num">${pct(r.accuracy)}</td><td class="num">${pct(r.pod)}</td><td class="num">${pct(r.far)}</td><td class="num">${fmt(r.brier, 3)}</td>`
        : `<td class="num">${fmt(r.mae, 2, unit)}</td><td class="num">${r.bias > 0 ? "+" : ""}${fmt(r.bias, 2)}</td>${isTemp ? `<td class="num">${pct(r.within_1)}</td><td class="num">${pct(r.within_2)}</td>` : ""}`;
      return `<tr><td class="${mname === best ? "best" : ""}">${esc(mname)}${mname === t.selected ? " ✓" : ""}</td>${cells}</tr>`; }).join("");
    return `<div><h3 style="font-size:15px;margin:6px 0">${esc(t.name)}<span class="muted" style="font-weight:400"> · 明天預報 · 正確答案：${esc(t.truth)}</span></h3>
      <div class="table-wrap"><table><tr><th>方法</th>${head}</tr>${rows}</table></div></div>`;
  });
  $("#verify").innerHTML = `<div class="grid-2">${parts.join("")}</div>
    <p class="muted" style="margin-top:10px">✓ = 系統採用的方法（在另一段資料上選出，不是看考卷挑的）；綠字 = 考卷期實際最好的方法。</p>
    <ul class="plain muted">${v.explanation.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
}

// ---------------------------------------------------------------- 圖表工具
function baseOptions(scales, label, extra, legend = true) {
  const grid = css("--line"), text = css("--muted");
  const s = { x: { grid: { color: grid }, ticks: { color: text }, ...(extra?.x || {}) } };
  Object.entries(scales).forEach(([k, v]) => { s[k] = { grid: { color: grid }, ticks: { color: text, ...(v.ticks || {}) }, ...v,
    title: v.title ? { color: text, ...v.title } : undefined }; s[k].ticks = { color: text, ...(v.ticks || {}) }; });
  return {
    responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, scales: s,
    plugins: { legend: { display: legend, labels: { color: css("--text"), boxWidth: 12 } },
      tooltip: label ? { callbacks: { label } } : {} },
  };
}
function drawChart(id, sel, cfg) {
  if (typeof Chart === "undefined") return;
  charts[id]?.destroy();
  charts[id] = new Chart($(sel), cfg);
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => data && render());

// 回到上次查詢的地點
try { const last = JSON.parse(localStorage.getItem("wx:last") || "null"); if (last) { q.value = last.name; load(last); } } catch {}

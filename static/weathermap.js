"use strict";
// 天氣地圖：色彩場 + 風場粒子動畫 + 時間軸播放 + 颱風路徑 + 雷達
const WeatherMap = (() => {
  const LAYERS = {
    temp: { name: "溫度", unit: "°C", digits: 1, stops: [[-40, "#e6d0ff"], [-30, "#8c4fc9"], [-20, "#4b3ba8"], [-10, "#3165c9"], [0, "#5fb2e8"], [5, "#7fd4c8"], [10, "#8ed78d"], [15, "#c8e06e"], [20, "#f2d65b"], [25, "#f3a33f"], [30, "#e4572e"], [35, "#b8233a"], [40, "#7a1240"], [48, "#3d0a24"]] },
    precip: { name: "降水量", unit: "mm／3小時", digits: 1, stops: [[0, "rgba(0,0,0,0)"], [0.15, "rgba(120,180,255,0)"], [0.3, "rgba(120,180,255,.55)"], [1, "#4f8ff0"], [3, "#2a56d6"], [6, "#7b3fd1"], [12, "#c23aa8"], [25, "#e8384f"], [50, "#ff9d00"], [100, "#fff200"]] },
    wind: { name: "風速", unit: "km/h", digits: 0, stops: [[0, "#5a6fb3"], [10, "#3c94c8"], [20, "#45b59a"], [30, "#87c55a"], [45, "#e1c547"], [60, "#ea8c3a"], [80, "#d9493e"], [100, "#b2307a"], [130, "#7b2cbf"], [170, "#e9dcff"]] },
    gust: { name: "陣風", unit: "km/h", digits: 0, stops: [[0, "#5a6fb3"], [15, "#3c94c8"], [30, "#45b59a"], [45, "#87c55a"], [60, "#e1c547"], [80, "#ea8c3a"], [100, "#d9493e"], [120, "#b2307a"], [150, "#7b2cbf"], [200, "#e9dcff"]] },
    pressure: { name: "氣壓", unit: "hPa", digits: 0, isobars: true, stops: [[950, "#6a1b9a"], [970, "#3949ab"], [990, "#1e88e5"], [1000, "#4fc3f7"], [1008, "#a5d6a7"], [1013, "#e6ee9c"], [1020, "#ffcc80"], [1030, "#ff8a65"], [1045, "#c62828"]] },
    cloud: { name: "雲量", unit: "%", digits: 0, stops: [[0, "#2f5d8a"], [20, "#4a7298"], [50, "#8297ad"], [80, "#c4ccd6"], [100, "#eef1f4"]] },
    humidity: { name: "濕度", unit: "%", digits: 0, stops: [[0, "#a0522d"], [25, "#d2a05a"], [45, "#e8dc8a"], [65, "#8fd19e"], [80, "#3fa7c9"], [95, "#2251a8"], [100, "#1a2f7a"]] },
    radar: { name: "雷達", unit: "實測回波", radar: true },
  };
  if (window.WX_STATIC) delete LAYERS.radar;  // 離線版無法即時取得雷達圖
  // 離線版沒有底圖服務，自行標示主要城市
  const CITIES = [["台北", 25.04, 121.56], ["台中", 24.15, 120.68], ["台南", 22.99, 120.21], ["高雄", 22.62, 120.31],
    ["花蓮", 23.98, 121.6], ["台東", 22.76, 121.14], ["宜蘭", 24.75, 121.75], ["澎湖", 23.57, 119.58], ["金門", 24.43, 118.32],
    ["福州", 26.07, 119.3], ["廈門", 24.48, 118.09], ["上海", 31.23, 121.47], ["香港", 22.32, 114.17], ["廣州", 23.13, 113.26],
    ["馬尼拉", 14.6, 120.98], ["那霸", 26.21, 127.68], ["石垣島", 24.34, 124.16], ["沖繩", 26.5, 128.0], ["東京", 35.68, 139.69],
    ["首爾", 37.57, 126.98], ["溫州", 28.0, 120.67], ["汕頭", 23.35, 116.68], ["呂宋島", 16.5, 121.2], ["鹿兒島", 31.6, 130.56]];
  const DIRS = ["北", "東北", "東", "東南", "南", "西南", "西", "西北"];
  const ICON = { temp: "🌡", precip: "☔", wind: "💨", gust: "🌬", pressure: "◎", cloud: "☁", humidity: "💧", radar: "📡" };
  const PLAY = '<svg viewBox="0 0 24 24" width="18" height="18"><path d="M7 4.5v15l13-7.5z" fill="currentColor"/></svg>';
  const PAUSE = '<svg viewBox="0 0 24 24" width="18" height="18"><rect x="6" y="4.5" width="4" height="15" rx="1" fill="currentColor"/><rect x="14" y="4.5" width="4" height="15" rx="1" fill="currentColor"/></svg>';
  const CAT_COLOR = { TD: "#5ebaff", TS: "#00e5d4", HU: "#ffb020" };

  let map, root, fieldCanvas, fieldCtx, partCanvas, partCtx, offCanvas;
  let grid = null, layerKey = "temp", t = 0, playing = false, lastDrawT = -1, showParticles = false;  // 風線預設關閉，可在工具列開啟
  let particles = [], lonOf = [], latOf = [], W = 0, H = 0, moving = false;
  let tz = "UTC", locMarker = null, gridRect = null, loadingKey = null;
  let cyclones = [], stormLayer = null, stormIcons = [];
  let radar = { frames: [], layers: [], past: 0, host: "" };
  let lut = null, lutFor = null;
  let lastTs = 0, rafId = null, hoverLatLng = null, shownKey = null;

  // ------------------------------------------------------------ 色階
  function parseColor(c) {
    const el = document.createElement("canvas").getContext("2d");
    el.fillStyle = c; const s = el.fillStyle;
    if (s.startsWith("#")) return [parseInt(s.slice(1, 3), 16), parseInt(s.slice(3, 5), 16), parseInt(s.slice(5, 7), 16), 255];
    const m = s.match(/[\d.]+/g).map(Number);
    return [m[0], m[1], m[2], Math.round((m[3] ?? 1) * 255)];
  }
  function buildLut(key) {
    const stops = LAYERS[key].stops.map(([v, c]) => [v, parseColor(c)]);
    const lo = stops[0][0], hi = stops[stops.length - 1][0], n = 4096, table = new Uint8ClampedArray(n * 4);
    for (let k = 0; k < n; k++) {
      const v = lo + ((hi - lo) * k) / (n - 1);
      let s = 1; while (s < stops.length - 1 && v > stops[s][0]) s++;
      const [v0, c0] = stops[s - 1], [v1, c1] = stops[s], f = Math.min(1, Math.max(0, (v - v0) / (v1 - v0 || 1)));
      for (let ch = 0; ch < 4; ch++) table[k * 4 + ch] = c0[ch] + (c1[ch] - c0[ch]) * f;
    }
    return { lo, hi, n, table };
  }
  function colorOf(v) {
    if (lutFor !== layerKey) { lut = buildLut(layerKey); lutFor = layerKey; }
    const k = Math.round(((v - lut.lo) / (lut.hi - lut.lo)) * (lut.n - 1));
    return Math.max(0, Math.min(lut.n - 1, k)) * 4;
  }

  // ------------------------------------------------------------ 內插
  function normLon(lon) {
    const lo0 = grid.lons[0];
    while (lon < lo0 - 180) lon += 360;
    while (lon > lo0 + 180) lon -= 360;
    return lon;
  }
  function sample(name, lat, lon, tt = t) {
    if (!grid) return NaN;
    const f = grid.fields[name], ny = grid.lats.length, nx = grid.lons.length;
    const fy = ((lat - grid.lats[0]) / (grid.lats[ny - 1] - grid.lats[0])) * (ny - 1);
    const fx = ((normLon(lon) - grid.lons[0]) / (grid.lons[nx - 1] - grid.lons[0])) * (nx - 1);
    if (fy < 0 || fx < 0 || fy > ny - 1 || fx > nx - 1) return NaN;
    const i0 = Math.min(Math.floor(fy), ny - 2), j0 = Math.min(Math.floor(fx), nx - 2), dy = fy - i0, dx = fx - j0;
    const k0 = Math.min(Math.floor(tt), f.length - 1), k1 = Math.min(k0 + 1, f.length - 1), dt = tt - k0;
    const at = (k) => {
      const fr = f[k], a = fr[i0 * nx + j0], b = fr[i0 * nx + j0 + 1], c = fr[(i0 + 1) * nx + j0], d = fr[(i0 + 1) * nx + j0 + 1];
      if (a === null || b === null || c === null || d === null) return NaN;
      return (a * (1 - dx) + b * dx) * (1 - dy) + (c * (1 - dx) + d * dx) * dy;
    };
    const v0 = at(k0);
    return dt > 0 && k1 !== k0 ? v0 + (at(k1) - v0) * dt : v0;
  }

  // ------------------------------------------------------------ 畫布
  function resize() {
    const s = map.getSize();
    W = s.x; H = s.y;
    for (const c of [fieldCanvas, partCanvas]) { c.width = W; c.height = H; c.style.width = W + "px"; c.style.height = H + "px"; }
    lonOf = new Float64Array(W); latOf = new Float64Array(H);
    for (let x = 0; x < W; x++) lonOf[x] = map.containerPointToLatLng([x, 0]).lng;
    for (let y = 0; y < H; y++) latOf[y] = map.containerPointToLatLng([0, y]).lat;
    const origin = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(fieldCanvas, origin);
    L.DomUtil.setPosition(partCanvas, origin);
  }

  function drawField() {
    lastDrawT = t;
    fieldCtx.clearRect(0, 0, W, H);
    if (!grid || LAYERS[layerKey].radar) return;
    const S = 4, w = Math.ceil(W / S), h = Math.ceil(H / S);
    offCanvas.width = w; offCanvas.height = h;
    const octx = offCanvas.getContext("2d"), img = octx.createImageData(w, h), px = img.data;
    const name = layerKey;
    sample(name, 0, 0); colorOf(0);
    for (let y = 0; y < h; y++) {
      const lat = latOf[Math.min(H - 1, y * S + 2)];
      for (let x = 0; x < w; x++) {
        const v = sample(name, lat, lonOf[Math.min(W - 1, x * S + 2)]);
        if (Number.isNaN(v)) continue;
        const c = colorOf(v), o = (y * w + x) * 4;
        px[o] = lut.table[c]; px[o + 1] = lut.table[c + 1]; px[o + 2] = lut.table[c + 2]; px[o + 3] = lut.table[c + 3] * 0.82;
      }
    }
    octx.putImageData(img, 0, 0);
    fieldCtx.imageSmoothingEnabled = true;
    fieldCtx.drawImage(offCanvas, 0, 0, w * S, h * S);
    if (LAYERS[layerKey].isobars) drawIsobars();
  }

  function drawIsobars() {
    if (typeof d3 === "undefined" || !d3.contours) return;
    const ny = grid.lats.length, nx = grid.lons.length, vals = new Array(nx * ny);
    const lat0 = grid.lats[0], dlat = (grid.lats[ny - 1] - lat0) / (ny - 1), lon0 = grid.lons[0], dlon = (grid.lons[nx - 1] - lon0) / (nx - 1);
    for (let i = 0; i < ny; i++) for (let j = 0; j < nx; j++) vals[i * nx + j] = sample("pressure", lat0 + i * dlat, lon0 + j * dlon) || 1013;
    const lo = Math.floor(Math.min(...vals) / 4) * 4, hi = Math.ceil(Math.max(...vals) / 4) * 4;
    const th = []; for (let v = lo; v <= hi; v += 4) th.push(v);
    const toPt = ([x, y]) => map.latLngToContainerPoint([lat0 + (y - 0.5) * dlat, lon0 + (x - 0.5) * dlon]);
    fieldCtx.lineWidth = 1.2; fieldCtx.strokeStyle = "rgba(255,255,255,.75)";
    fieldCtx.font = "bold 11px sans-serif"; fieldCtx.fillStyle = "#fff"; fieldCtx.textAlign = "center";
    d3.contours().size([nx, ny]).thresholds(th)(vals).forEach((c) => {
      c.coordinates.forEach((poly) => poly.forEach((ring) => {
        const pts = ring.filter(([x, y]) => x > 0.6 && y > 0.6 && x < nx - 0.6 && y < ny - 0.6).map(toPt);
        if (pts.length < 3) return;
        fieldCtx.beginPath();
        pts.forEach((p, k) => (k ? fieldCtx.lineTo(p.x, p.y) : fieldCtx.moveTo(p.x, p.y)));
        fieldCtx.stroke();
        const mid = pts[Math.floor(pts.length / 2)];
        if (pts.length > 12) fieldCtx.fillText(c.value, mid.x, mid.y - 3);
      }));
    });
  }

  // ------------------------------------------------------------ 風場粒子
  function spawn(p) {
    p.x = Math.random() * W; p.y = Math.random() * H; p.age = 0; p.max = 40 + Math.random() * 80;
    return p;
  }
  function resetParticles() {
    partCtx.clearRect(0, 0, W, H);
    const n = Math.min(4500, Math.round((W * H) / 260));
    particles = Array.from({ length: n }, () => spawn({}));
  }
  function stepParticles() {
    partCtx.globalCompositeOperation = "destination-in";
    partCtx.fillStyle = "rgba(0,0,0,0.91)";
    partCtx.fillRect(0, 0, W, H);
    partCtx.globalCompositeOperation = "source-over";
    if (!showParticles || !grid || moving || LAYERS[layerKey].radar) return;
    partCtx.strokeStyle = layerKey === "cloud" || layerKey === "humidity" ? "rgba(20,30,60,.55)" : "rgba(255,255,255,.8)";
    partCtx.lineWidth = 1.1;
    partCtx.beginPath();
    const k = 0.16;
    for (const p of particles) {
      const xi = p.x | 0, yi = p.y | 0;
      if (xi < 0 || yi < 0 || xi >= W || yi >= H) { spawn(p); continue; }
      const u = sample("u", latOf[yi], lonOf[xi]), v = sample("v", latOf[yi], lonOf[xi]);
      if (Number.isNaN(u) || Number.isNaN(v) || p.age++ > p.max) { spawn(p); continue; }
      const nx = p.x + u * k, ny = p.y - v * k;
      partCtx.moveTo(p.x, p.y); partCtx.lineTo(nx, ny);
      p.x = nx; p.y = ny;
    }
    partCtx.stroke();
  }

  // ------------------------------------------------------------ 時間軸
  const fmtTime = (iso, opts) => new Intl.DateTimeFormat("zh-TW", { timeZone: tz, hour12: false, ...opts }).format(new Date(iso));
  function frameTime(tt) {
    if (LAYERS[layerKey].radar) return radar.frames[Math.round(tt)] ? radar.frames[Math.round(tt)].time * 1000 : Date.now();
    const k = Math.floor(tt), a = Date.parse(grid.times[k]), b = Date.parse(grid.times[Math.min(k + 1, grid.times.length - 1)]);
    return a + (b - a) * (tt - k);
  }
  function maxT() { return LAYERS[layerKey].radar ? Math.max(0, radar.frames.length - 1) : grid ? grid.times.length - 1 : 0; }

  function buildTimeline() {
    const slider = root.querySelector(".wm-slider"), days = root.querySelector(".wm-days");
    slider.max = maxT();
    slider.step = LAYERS[layerKey].radar ? 1 : 0.01;
    if (LAYERS[layerKey].radar) {
      days.innerHTML = `<span style="flex:1">雷達回波（過去 2 小時實測，每 10 分鐘）</span>`;
      return;
    }
    const groups = [];
    grid.times.forEach((iso, k) => {
      const key = fmtTime(iso, { month: "numeric", day: "numeric" });
      if (!groups.length || groups[groups.length - 1].key !== key) groups.push({ key, iso, start: k, n: 0 });
      groups[groups.length - 1].n++;
    });
    days.innerHTML = groups.map((g) => `<button data-k="${g.start}" style="flex:${g.n}">${fmtTime(g.iso, { weekday: "short" }).replace("週", "")} ${g.key.split("/")[1]}</button>`).join("");
    days.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => setT(+b.dataset.k + 3)));
  }
  function setT(v) {
    t = Math.max(0, Math.min(maxT(), v));
    root.querySelector(".wm-slider").value = t;
    if (LAYERS[layerKey].radar) showRadarFrame(Math.round(t));
    updateLabel();
    updateStorms();
    updateHover();
    if (Math.abs(t - lastDrawT) > 0.03) drawField();
  }
  function updateLabel() {
    const ms = frameTime(t);
    root.querySelector(".wm-time").textContent = fmtTime(ms, { weekday: "short", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function togglePlay(on = !playing) {
    playing = on;
    root.querySelector(".wm-play").innerHTML = playing ? PAUSE : PLAY;
    root.querySelector(".wm-play").setAttribute("aria-label", playing ? "暫停" : "播放");
  }

  function loop(ts) {
    const dt = Math.min(0.1, (ts - (lastTs || ts)) / 1000);
    lastTs = ts;
    if (playing) {
      const speed = LAYERS[layerKey].radar ? 1.6 : 2.2;   // 每秒前進幾個時間格
      let nt = t + dt * speed;
      if (nt > maxT()) nt = 0;
      if (LAYERS[layerKey].radar) { if (Math.floor(nt) !== Math.floor(t)) setT(Math.floor(nt)); t = nt; }
      else setT(nt);
    }
    stepParticles();
    rafId = requestAnimationFrame(loop);
  }

  // ------------------------------------------------------------ 圖例 / 游標讀值
  function buildLegend() {
    const L_ = LAYERS[layerKey], el = root.querySelector(".wm-legend");
    if (L_.radar) {
      el.innerHTML = `<div class="wm-lg-u">回波強度</div><div class="wm-lg-body"><div class="wm-lg-tk"><span style="bottom:100%">強</span><span style="bottom:50%">中</span><span style="bottom:0%">弱</span></div>
        <div class="wm-lg-bar" style="background:linear-gradient(0deg,#88ddee,#0099cc,#0077aa,#ffee00,#ffaa00,#ff4400,#c10000,#ff00ff)"></div></div>`;
      return;
    }
    // 直式色階：節點等距排列，非線性色階（如雨量）的刻度才不會擠在一起
    const s = L_.stops.filter(([, c]) => !/rgba\(.*,\s*0\)$/.test(c)), n = s.length - 1;
    const grad = s.map(([, c], k) => `${c} ${(k / n) * 100}%`).join(",");
    const ticks = s.map(([v], k) => `<span style="bottom:${(k / n) * 100}%">${v}</span>`).join("");
    el.innerHTML = `<div class="wm-lg-u">${L_.unit}</div><div class="wm-lg-body"><div class="wm-lg-tk">${ticks}</div>
      <div class="wm-lg-bar" style="background:linear-gradient(0deg,${grad})"></div></div>`;
  }
  function updateHover() {
    const box = root.querySelector(".wm-read");
    if (!hoverLatLng || !grid || LAYERS[layerKey].radar) { box.hidden = true; return; }
    const { lat, lng } = hoverLatLng, L_ = LAYERS[layerKey];
    const v = sample(layerKey, lat, lng);
    if (Number.isNaN(v)) { box.hidden = true; return; }
    const u = sample("u", lat, lng), w = sample("v", lat, lng);
    const spd = Math.hypot(u, w) * 3.6, from = (Math.atan2(-u, -w) * 180) / Math.PI;
    box.hidden = false;
    box.innerHTML = `<b>${v.toFixed(L_.digits)} ${L_.unit.split("／")[0]}</b><span>${L_.name}</span>
      <span>風 ${DIRS[Math.round(((from + 360) % 360) / 45) % 8]} ${spd.toFixed(0)} km/h · ${lat.toFixed(2)}, ${lng.toFixed(2)}</span>`;
  }

  // ------------------------------------------------------------ 颱風
  // 颱風外觀：螺旋雲帶 + 眼牆 + 颱風眼，大小依該時刻的 7 級風暴風半徑換算成畫面像素，
  // 北半球逆時針旋轉、南半球順時針；強度越強轉得越快、颱風眼越清楚。
  function spiralArm(offset, turns, width) {
    const n = 48, outer = [], inner = [];
    for (let k = 0; k <= n; k++) {
      const f = k / n, th = offset + f * turns * 2 * Math.PI;
      const r = 12 + 88 * Math.pow(f, 1.25), w = width * Math.sin(Math.PI * Math.min(1, f * 1.4 + 0.15)) + 1;
      outer.push([(r + w) * Math.cos(th), (r + w) * Math.sin(th)]);
      inner.push([(r - w) * Math.cos(th), (r - w) * Math.sin(th)]);
    }
    const pts = outer.concat(inner.reverse()).map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`);
    return `M${pts.join("L")}Z`;
  }
  // 三條主雲帶（越外圍越細），像衛星雲圖上的颱風螺旋雨帶
  const ARMS = [[0, 0.62, 13], [2.1, 0.55, 10], [4.2, 0.5, 8]].map(([o, turns, w]) => spiralArm(o, turns, w));
  function stormHtml(cat, south, px) {
    const strong = cat === "HU", eye = strong ? 7 : 0;
    const spin = strong ? 7 : cat === "TS" ? 11 : 16;   // 秒／圈
    return `<div class="wm-ty" style="width:${px * 2}px;height:${px * 2}px">
      <svg viewBox="-100 -100 200 200" width="100%" height="100%" aria-hidden="true">
        <g class="wm-ty-rot" style="animation-duration:${spin}s;animation-direction:${south ? "normal" : "reverse"}">
          <g transform="${south ? "scale(1,-1)" : ""}">
            <circle r="98" fill="url(#wm-ty-haze)"/>
            <g filter="url(#wm-ty-blur)" fill="#fff" opacity="${strong ? 0.85 : 0.65}">${ARMS.map((d) => `<path d="${d}"/>`).join("")}</g>
            <circle r="${eye + 10}" fill="none" stroke="#fff" stroke-width="${strong ? 12 : 7}" opacity="${strong ? 0.9 : 0.6}" filter="url(#wm-ty-blur)"/>
            ${eye ? `<circle r="${eye}" fill="#1c2638" opacity=".75"/>` : ""}
          </g>
        </g>
      </svg></div>`;
  }
  function ensureStormDefs() {
    if (document.getElementById("wm-ty-defs")) return;
    document.body.insertAdjacentHTML("beforeend", `<svg id="wm-ty-defs" width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
      <radialGradient id="wm-ty-haze"><stop offset="0" stop-color="#fff" stop-opacity=".55"/><stop offset=".35" stop-color="#fff" stop-opacity=".22"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
      <filter id="wm-ty-blur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="2.4"/></filter></defs></svg>`);
  }
  const kmToPx = (lat, lon, km) => Math.abs(map.latLngToContainerPoint([lat, lon]).y - map.latLngToContainerPoint([lat + km / 111, lon]).y);

  async function loadCyclones() {
    try {
      const r = await apiFetch("/api/map/cyclones");
      cyclones = r.ok ? await r.json() : [];
    } catch { cyclones = []; }
    drawStorms();
  }
  function drawStorms() {
    ensureStormDefs();
    if (stormLayer) stormLayer.remove();
    stormLayer = L.layerGroup().addTo(map);
    stormIcons = [];
    const ref = map.getCenter().lng;
    const fixLon = (lon) => { while (lon - ref > 180) lon -= 360; while (lon - ref < -180) lon += 360; return lon; };
    cyclones.forEach((s) => {
      const pts = s.points.map((p) => ({ ...p, lon: fixLon(p.lon), ms: Date.parse(p.time) }));
      if (s.cone) L.polygon(s.cone.map(([lo, la]) => [la, fixLon(lo)]), { pane: "storm", color: "#fff", weight: 1, dashArray: "4 4", fillColor: "#fff", fillOpacity: 0.12 }).addTo(stormLayer);
      const past = pts.filter((p) => !p.forecast), fut = pts.filter((p) => p.forecast);
      if (past.length > 1) L.polyline(past.map((p) => [p.lat, p.lon]), { pane: "storm", color: "#fff", weight: 2.5, opacity: 0.9 }).addTo(stormLayer);
      if (fut.length) L.polyline([past[past.length - 1] || fut[0], ...fut].map((p) => [p.lat, p.lon]), { pane: "storm", color: "#fff", weight: 2.5, dashArray: "7 6" }).addTo(stormLayer);
      pts.forEach((p) => {
        const m = L.circleMarker([p.lat, p.lon], { pane: "storm", radius: p.forecast ? 5 : 3.5, color: "#222", weight: 1, fillColor: CAT_COLOR[p.category] || "#ccc", fillOpacity: 1 })
          .bindTooltip(`<b>${s.name}</b> ${p.forecast ? "預報" : "實際"}<br>${fmtTime(p.ms, { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}<br>${p.category_name}<br>7 級風暴風半徑約 ${p.r60} km`)
          .addTo(stormLayer);
        if (p.forecast) m.bindTooltip(fmtTime(p.ms, { month: "numeric", day: "numeric", hour: "2-digit" }), { permanent: true, direction: "right", className: "wm-tlabel", offset: [6, 0] });
      });
      // 颱風本體放在路徑線下方的圖層，雲帶才不會蓋住路徑
      const icon = L.marker([pts[0].lat, pts[0].lon], { pane: "typhoon", interactive: false, icon: L.divIcon({ html: "", className: "wm-ty-box", iconSize: [0, 0] }) });
      const label = L.marker([pts[0].lat, pts[0].lon], { pane: "storm", interactive: false, icon: L.divIcon({ html: "", className: "wm-ty-label", iconSize: [0, 0] }) });
      stormIcons.push({ s, pts, icon, label, px: 0, cat: null });
    });
    updateStorms();
  }
  function updateStorms() {
    if (!stormIcons.length || (!grid && !LAYERS[layerKey].radar)) return;
    const now = LAYERS[layerKey].radar ? Date.now() : frameTime(t);
    stormIcons.forEach((st) => {
      const { s, pts, icon, label } = st;
      if (now < pts[0].ms || now > pts[pts.length - 1].ms) {
        if (map.hasLayer(icon)) { icon.remove(); label.remove(); }
        return;
      }
      let k = 0; while (k < pts.length - 2 && pts[k + 1].ms < now) k++;
      const a = pts[k], b = pts[k + 1], f = Math.min(1, Math.max(0, (now - a.ms) / (b.ms - a.ms || 1)));
      const lat = a.lat + (b.lat - a.lat) * f, lon = a.lon + (b.lon - a.lon) * f;
      const r60 = a.r60 + (b.r60 - a.r60) * f, cat = f < 0.5 ? a.category : b.category;
      icon.setLatLng([lat, lon]); label.setLatLng([lat, lon]);
      if (!map.hasLayer(icon)) { icon.addTo(stormLayer); label.addTo(stormLayer); st.px = 0; st.cat = null; }
      // 雲系外觀半徑約為 7 級風半徑的 0.85 倍，並設上下限避免過小或蓋滿畫面
      const px = Math.max(18, Math.min(460, kmToPx(lat, lon, Math.max(100, r60 * 0.85))));
      const el = icon.getElement();
      if (!el) return;
      if (cat !== st.cat) {
        el.innerHTML = stormHtml(cat, lat < 0, px);
        st.cat = cat; st.px = px;
        label.getElement().innerHTML = `<span><b>${s.name}</b>${_CAT_SHORT[cat] || ""}</span>`;
      } else if (Math.abs(px - st.px) > 1.5) {
        const box = el.firstElementChild;
        box.style.width = box.style.height = `${px * 2}px`;
        st.px = px;
      }
      el.firstElementChild.style.transform = `translate(${-px}px, ${-px}px)`;
    });
  }
  const _CAT_SHORT = { HU: "颱風", TS: "熱帶風暴", TD: "熱帶性低氣壓" };

  // ------------------------------------------------------------ 雷達（RainViewer）
  async function loadRadar() {
    if (radar.frames.length) return;
    try {
      const r = await fetch("https://api.rainviewer.com/public/weather-maps.json");
      const j = await r.json();
      radar.host = j.host;
      radar.past = (j.radar.past || []).length;
      radar.frames = (j.radar.past || []).concat(j.radar.nowcast || []);
    } catch { radar.frames = []; }
  }
  function showRadarFrame(k) {
    if (!radar.frames.length) return;
    k = Math.max(0, Math.min(radar.frames.length - 1, k));
    radar.frames.forEach((f, i) => {
      if (Math.abs(i - k) > 1 && !radar.layers[i]) return;
      if (!radar.layers[i]) {
        radar.layers[i] = L.tileLayer(`${radar.host}${f.path}/256/{z}/{x}/{y}/2/1_1.png`, { pane: "field", opacity: 0, maxNativeZoom: 7, maxZoom: 12, attribution: "RainViewer" }).addTo(map);
      }
      radar.layers[i].setOpacity(i === k ? 0.85 : 0);
    });
  }
  function hideRadar() { radar.layers.forEach((l) => l && l.setOpacity(0)); }

  // ------------------------------------------------------------ 格點資料
  async function loadGrid(lat, lng) {
    const key = `${Math.round(lat / 6)}_${Math.round(lng / 6)}`;
    if (key === loadingKey) return;
    loadingKey = key;
    root.querySelector(".wm-loading").hidden = false;
    try {
      const r = await apiFetch(`/api/map/grid?lat=${lat.toFixed(2)}&lon=${lng.toFixed(2)}`);
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
      const g = await r.json();
      const firstLoad = !grid;
      const keepTime = grid ? frameTime(t) : Date.now();
      grid = g;
      if (gridRect) gridRect.remove();
      gridRect = L.rectangle([[g.lats[0], g.lons[0]], [g.lats[g.lats.length - 1], g.lons[g.lons.length - 1]]], { pane: "storm", color: "#fff", weight: 1, opacity: 0.35, dashArray: "3 6", fill: false, interactive: false }).addTo(map);
      buildTimeline();
      let k = g.times.findIndex((iso) => Date.parse(iso) > keepTime);
      k = k <= 0 ? 0 : k - 1 + (keepTime - Date.parse(g.times[k - 1])) / (Date.parse(g.times[k]) - Date.parse(g.times[k - 1]));
      setT(k);
      drawField();
      if (firstLoad) resetParticles();
      root.querySelector(".wm-err").hidden = true;
    } catch (e) {
      loadingKey = null;
      const err = root.querySelector(".wm-err");
      err.textContent = "地圖資料載入失敗：" + e.message;
      err.hidden = false;
    } finally {
      root.querySelector(".wm-loading").hidden = true;
    }
  }
  function maybeReload() {
    if (!grid) return;
    const c = map.getCenter(), ny = grid.lats.length, nx = grid.lons.length;
    const latMid = (grid.lats[0] + grid.lats[ny - 1]) / 2, lonMid = (grid.lons[0] + grid.lons[nx - 1]) / 2;
    if (Math.abs(c.lat - latMid) > 6 || Math.abs(normLon(c.lng) - lonMid) > 7) loadGrid(c.lat, normLon(c.lng));
  }

  // ------------------------------------------------------------ 圖層切換
  async function setLayer(key) {
    layerKey = key;
    root.querySelectorAll(".wm-layers button[data-k]").forEach((b) => b.classList.toggle("on", b.dataset.k === key));
    buildLegend();
    if (LAYERS[key].radar) {
      await loadRadar();
      buildTimeline();
      setT(Math.max(0, radar.past - 1));  // 從最新一張實測開始
      fieldCtx.clearRect(0, 0, W, H);
      if (map.getZoom() > 7) map.setZoom(7);
    } else {
      hideRadar();
      buildTimeline();
      if (t > maxT()) t = 0;
      lastDrawT = -1;
      setT(t);
    }
  }

  // ------------------------------------------------------------ 初始化
  function init(el) {
    root = el;
    root.innerHTML = `
      <div class="wm-map"></div>
      <div class="wm-layers" role="toolbar" aria-label="圖層">${Object.entries(LAYERS).map(([k, l]) => `<button data-k="${k}" class="${k === layerKey ? "on" : ""}"><span>${ICON[k]}</span>${l.name}</button>`).join("")}
        <button class="wm-part" title="風場粒子動畫"><span class="sw"></span>風線</button></div>
      <div class="wm-read" hidden></div>
      <div class="wm-loading" hidden>載入格點資料…</div>
      <div class="wm-err" hidden></div>
      <div class="wm-legend"></div>
      <div class="wm-bar">
        <button class="wm-play" aria-label="播放">${PLAY}</button>
        <div class="wm-track"><div class="wm-days"></div><input class="wm-slider" type="range" min="0" max="1" step="0.01" value="0" aria-label="時間"></div>
        <div class="wm-side"><div class="wm-time"></div>
          <div class="wm-steps"><button class="wm-step" data-d="-1" aria-label="上一個時間">−3h</button><button class="wm-step" data-d="1" aria-label="下一個時間">+3h</button></div></div>
      </div>`;
    map = L.map(root.querySelector(".wm-map"), { worldCopyJump: true, zoomControl: false, minZoom: 3, maxZoom: 10 });
    L.control.zoom({ position: "topright" }).addTo(map);
    map.createPane("field").style.zIndex = 300;
    map.createPane("particles").style.zIndex = 330;
    map.createPane("borders").style.zIndex = 340;
    map.createPane("labels").style.zIndex = 350;
    map.createPane("typhoon").style.zIndex = 360;
    map.createPane("storm").style.zIndex = 450;
    for (const p of ["field", "particles", "borders", "labels", "typhoon"]) map.getPane(p).style.pointerEvents = "none";
    if (window.WX_STATIC) {
      // 離線版：陸地由海岸線資料自行繪製，城市名稱用標籤
      map.createPane("land").style.zIndex = 250;
      CITIES.forEach(([n, la, lo]) => L.marker([la, lo], { pane: "labels", interactive: false,
        icon: L.divIcon({ className: "wm-city", html: `<i></i><span>${n}</span>`, iconSize: [8, 8], iconAnchor: [4, 4] }) }).addTo(map));
      map.attributionControl.addAttribution("海岸線 © Natural Earth");
    } else {
      const esri = "https://server.arcgisonline.com/ArcGIS/rest/services/";
      L.tileLayer(esri + "Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", { attribution: "底圖 © Esri", maxNativeZoom: 16 }).addTo(map);
      L.tileLayer(esri + "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", { pane: "labels", maxNativeZoom: 16, opacity: 0.95 }).addTo(map);
    }

    fieldCanvas = L.DomUtil.create("canvas", "wm-canvas", map.getPane("field"));
    partCanvas = L.DomUtil.create("canvas", "wm-canvas", map.getPane("particles"));
    offCanvas = document.createElement("canvas");
    fieldCtx = fieldCanvas.getContext("2d");
    partCtx = partCanvas.getContext("2d");

    map.on("movestart zoomstart", () => { moving = true; partCtx.clearRect(0, 0, W, H); });
    map.on("zoomstart", () => { fieldCanvas.style.visibility = "hidden"; });
    map.on("moveend zoomend resize", () => {
      moving = false; fieldCanvas.style.visibility = "";
      resize(); drawField(); resetParticles(); drawStorms();
      clearTimeout(map._reloadTimer); map._reloadTimer = setTimeout(maybeReload, 500);
    });
    map.on("mousemove click", (e) => { hoverLatLng = e.latlng; updateHover(); });
    map.on("mouseout", () => { hoverLatLng = null; updateHover(); });

    root.querySelectorAll(".wm-layers button[data-k]").forEach((b) => b.addEventListener("click", () => setLayer(b.dataset.k)));
    root.querySelector(".wm-part").addEventListener("click", (e) => {
      showParticles = !showParticles; e.currentTarget.classList.toggle("on", showParticles);
      if (!showParticles) partCtx.clearRect(0, 0, W, H);
    });
    root.querySelector(".wm-play").addEventListener("click", () => togglePlay());
    root.querySelectorAll(".wm-step").forEach((b) => b.addEventListener("click", () => { togglePlay(false); setT(Math.round(t) + +b.dataset.d); }));
    root.querySelector(".wm-slider").addEventListener("input", (e) => { togglePlay(false); setT(+e.target.value); });
    addEventListener("keydown", (e) => {
      if (!root.matches(":hover") || e.target.tagName === "INPUT" && e.target.type !== "range") return;
      if (e.key === " ") { e.preventDefault(); togglePlay(); }
    });

    loadCoastlines();
    buildLegend();
    rafId = requestAnimationFrame(loop);
  }

  async function loadCoastlines() {
    if (typeof topojson === "undefined") return;
    try {
      const src = window.WX_STATIC ? "data/world-50m.json" : "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json";
      const topo = await (await fetch(src)).json();
      if (window.WX_STATIC) {
        L.geoJSON(topojson.feature(topo, topo.objects.countries), { pane: "land", interactive: false,
          style: { stroke: false, fillColor: "#3a3f47", fillOpacity: 1 } }).addTo(map);
        L.geoJSON(topojson.mesh(topo, topo.objects.countries, (a, b) => a !== b), { pane: "borders", interactive: false,
          style: { color: "#111", weight: 0.8, opacity: 0.5, dashArray: "3 3" } }).addTo(map);
      }
      // 國界與地名由 Esri 參考圖層提供，這裡補上海岸線讓陸地輪廓在色彩場上清楚可見
      const coast = topojson.mesh(topo, topo.objects.countries, (a, b) => a === b);
      L.geoJSON(coast, { pane: "borders", style: { color: "#1d1d1d", weight: 1, opacity: 0.6 }, interactive: false }).addTo(map);
    } catch { /* 海岸線載入失敗不影響地圖 */ }
  }

  // ------------------------------------------------------------ 對外介面
  function show(location) {
    const el = document.getElementById("weathermap");
    if (!map) init(el);
    const key = `${location.latitude},${location.longitude}`;
    if (key === shownKey) return;  // 同一地點重新繪製頁面時保留使用者目前的地圖視角
    shownKey = key;
    tz = location.timezone || "UTC";
    map.setView([location.latitude, location.longitude], 6, { animate: false });
    if (locMarker) locMarker.remove();
    locMarker = L.marker([location.latitude, location.longitude], { pane: "storm", icon: L.divIcon({ className: "", html: '<div class="wm-pin"></div>', iconSize: [14, 14], iconAnchor: [7, 7] }) })
      .bindTooltip(location.name, { direction: "top", offset: [0, -6] }).addTo(map);
    setTimeout(() => { map.invalidateSize(); resize(); }, 0);
    loadingKey = null;
    loadGrid(location.latitude, location.longitude);
    loadCyclones();
  }
  return { show };
})();

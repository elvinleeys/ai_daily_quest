// 정적 파일 + 기상청 단기예보 프록시. 키를 브라우저에 노출하지 않으려고 서버에서 호출해요.
// 실행: node --env-file=.env server.js  (Node 20.6+)
const http = require('http');
const fs = require('fs');
const path = require('path');

const KEY = process.env.KMA_SERVICE_KEY;
const PORT = process.env.PORT || 3000;
const BASE = 'https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0';

// 위경도 → 기상청 격자(nx, ny): 공식 Lambert 변환
function toGrid(lat, lon) {
  const D = Math.PI / 180, RE = 6371.00877 / 5.0;
  const s1 = 30 * D, s2 = 60 * D, olon = 126 * D, olat = 38 * D;
  const sn = Math.log(Math.cos(s1) / Math.cos(s2)) / Math.log(Math.tan(Math.PI / 4 + s2 / 2) / Math.tan(Math.PI / 4 + s1 / 2));
  const sf = Math.pow(Math.tan(Math.PI / 4 + s1 / 2), sn) * Math.cos(s1) / sn;
  const ro = RE * sf / Math.pow(Math.tan(Math.PI / 4 + olat / 2), sn);
  const ra = RE * sf / Math.pow(Math.tan(Math.PI / 4 + lat * D / 2), sn);
  let th = lon * D - olon;
  if (th > Math.PI) th -= 2 * Math.PI;
  if (th < -Math.PI) th += 2 * Math.PI;
  th *= sn;
  return { nx: Math.floor(ra * Math.sin(th) + 43 + 0.5), ny: Math.floor(ro - ra * Math.cos(th) + 136 + 0.5) };
}

const p2 = n => String(n).padStart(2, '0');
// KST 기준 시각을 UTC getter로 읽기 위해 +9h 시프트
const kst = ms => new Date(ms + 9 * 36e5);
const fmtDate = d => `${d.getUTCFullYear()}${p2(d.getUTCMonth() + 1)}${p2(d.getUTCDate())}`;

async function kma(op, base, nx, ny, extra) {
  const q = new URLSearchParams({ serviceKey: KEY, pageNo: 1, numOfRows: 100, dataType: 'JSON', base_date: base.date, base_time: base.time, nx, ny, ...extra });
  const res = await fetch(`${BASE}/${op}?${q}`);
  const json = await res.json().catch(() => { throw new Error('KMA 응답 오류 (키/호출 한도 확인)'); });
  if (json.response.header.resultCode !== '00') throw new Error(json.response.header.resultMsg);
  return json.response.body.items.item;
}

async function weather(lat, lon) {
  const { nx, ny } = toGrid(lat, lon);
  const now = Date.now();
  // 실황: 매시 정각 생성, ~10분 후 제공 → 40분 전 시각의 정시
  const n = kst(now - 40 * 60000);
  const ncst = await kma('getUltraSrtNcst', { date: fmtDate(n), time: p2(n.getUTCHours()) + '00' }, nx, ny);
  // 초단기예보: 매시 30분 생성, ~15분 후 제공 → 45분 전 기준 직전 HH30
  let f = kst(now - 45 * 60000);
  if (f.getUTCMinutes() < 30) f = kst(f.getTime() - 36e5 - 9 * 36e5);
  const fcst = await kma('getUltraSrtFcst', { date: fmtDate(f), time: p2(f.getUTCHours()) + '30' }, nx, ny);

  const val = cat => (ncst.find(i => i.category === cat) || {}).obsrValue;
  const sky = fcst.filter(i => i.category === 'SKY').sort((a, b) => (a.fcstDate + a.fcstTime).localeCompare(b.fcstDate + b.fcstTime))[0];
  const pty = Number(val('PTY'));
  const kind = [3, 7].includes(pty) ? '눈' : pty ? '비' : sky && sky.fcstValue === '1' ? '맑음' : '흐림';
  return { temp: Math.round(Number(val('T1H'))), kind };
}

const html = path.join(__dirname, 'index.html');
http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  if (url.pathname === '/api/weather') {
    const lat = Number(url.searchParams.get('lat')), lon = Number(url.searchParams.get('lon'));
    res.setHeader('Content-Type', 'application/json');
    if (!KEY || !isFinite(lat) || !isFinite(lon)) { res.statusCode = 400; return res.end('{"error":"KMA_SERVICE_KEY 또는 좌표 없음"}'); }
    try { res.end(JSON.stringify(await weather(lat, lon))); }
    catch (e) { console.warn(e.message); res.statusCode = 502; res.end(JSON.stringify({ error: e.message })); }
  } else if (url.pathname === '/' || url.pathname === '/index.html') {
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    fs.createReadStream(html).pipe(res);
  } else { res.statusCode = 404; res.end(); }
}).listen(PORT, () => console.log(`http://localhost:${PORT}`));

"""정적 파일 + 기상청 단기예보 프록시 + Supabase 상태 저장 (표준 라이브러리만 사용). 실행: python server.py"""
import json
import math
import os
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
BASE = 'https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0'
KST = timezone(timedelta(hours=9))


def load_env():
    f = ROOT / '.env'
    if f.exists():
        for line in f.read_text(encoding='utf-8').splitlines():
            k, sep, v = line.partition('=')
            if sep and not k.strip().startswith('#'):
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))


load_env()
KEY = os.environ.get('KMA_SERVICE_KEY')
PORT = int(os.environ.get('PORT', 3000))
SB_URL = os.environ.get('SUPABASE_URL', '').rstrip('/')
SB_KEY = os.environ.get('SUPABASE_SERVICE_KEY')  # 서버에서만 사용 (RLS 우회) — 브라우저에 절대 내보내지 않기
MAX_STATE = 1_000_000

# index.html은 수정하지 않고, 내려줄 때 localStorage 저장을 Supabase와 동기화하는 스크립트를 끼워 넣는다.
# 초기 상태(REMOTE)를 localStorage에 먼저 써 두면 index.html의 동기 load()가 그대로 읽는다.
# settings(Gemini 키 등)는 DB로 보내지 않고 브라우저에 남긴다.
SHIM = """<script>(function(){var K='dailyQuest_v1',R=__REMOTE__,t;try{
function sync(v){clearTimeout(t);t=setTimeout(function(){try{var d=JSON.parse(v);delete d.settings;
fetch('/api/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d),keepalive:true})}catch(e){}},500)}
var l=localStorage.getItem(K);
if(R){var o=l&&JSON.parse(l);R.settings=(o&&o.settings)||{apiKey:'',model:''};localStorage.setItem(K,JSON.stringify(R))}
else if(l)sync(l);
var s=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){s.call(this,k,v);if(k===K)sync(v)}
}catch(e){}})()</script>"""


def sb(method, path, body=None, headers=None):
    req = urllib.request.Request(
        f'{SB_URL}/rest/v1/{path}', method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'apikey': SB_KEY, 'Authorization': f'Bearer {SB_KEY}', 'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=8) as res:
        return json.loads(res.read() or 'null')


def get_state(uid):
    rows = sb('GET', f'app_state?user_id=eq.{urllib.parse.quote(uid)}&select=data')
    return rows[0]['data'] if rows else None


def put_state(uid, data):
    sb('POST', 'app_state?on_conflict=user_id', {'user_id': uid, 'data': data, 'updated_at': datetime.now(timezone.utc).isoformat()},
       {'Prefer': 'resolution=merge-duplicates,return=minimal'})


def to_grid(lat, lon):
    """위경도 → 기상청 격자(nx, ny): 공식 Lambert 변환"""
    d = math.pi / 180
    re = 6371.00877 / 5.0
    s1, s2, olon, olat = 30 * d, 60 * d, 126 * d, 38 * d
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(math.tan(math.pi / 4 + s2 / 2) / math.tan(math.pi / 4 + s1 / 2))
    sf = math.tan(math.pi / 4 + s1 / 2) ** sn * math.cos(s1) / sn
    ro = re * sf / math.tan(math.pi / 4 + olat / 2) ** sn
    ra = re * sf / math.tan(math.pi / 4 + lat * d / 2) ** sn
    th = lon * d - olon
    th = (th + math.pi) % (2 * math.pi) - math.pi
    th *= sn
    return math.floor(ra * math.sin(th) + 43.5), math.floor(ro - ra * math.cos(th) + 136.5)


def kma(op, when, nx, ny):
    q = urllib.parse.urlencode({
        'serviceKey': KEY, 'pageNo': 1, 'numOfRows': 100, 'dataType': 'JSON',
        'base_date': when.strftime('%Y%m%d'), 'base_time': when.strftime('%H%M'), 'nx': nx, 'ny': ny,
    })
    with urllib.request.urlopen(f'{BASE}/{op}?{q}', timeout=8) as res:
        try:
            body = json.load(res)['response']
        except ValueError:
            raise RuntimeError('KMA 응답 오류 (키/호출 한도 확인)')
    if body['header']['resultCode'] != '00':
        raise RuntimeError(body['header']['resultMsg'])
    return body['body']['items']['item']


def weather(lat, lon):
    nx, ny = to_grid(lat, lon)
    now = datetime.now(KST)
    # 실황: 매시 정각 생성, ~10분 후 제공 → 40분 전 시각의 정시
    n = (now - timedelta(minutes=40)).replace(minute=0)
    ncst = kma('getUltraSrtNcst', n, nx, ny)
    # 초단기예보: 매시 30분 생성, ~15분 후 제공 → 45분 전 기준 직전 HH30
    f = now - timedelta(minutes=45)
    f = f.replace(minute=30) if f.minute >= 30 else (f - timedelta(hours=1)).replace(minute=30)
    fcst = kma('getUltraSrtFcst', f, nx, ny)

    obs = {i['category']: i['obsrValue'] for i in ncst}
    sky = min((i for i in fcst if i['category'] == 'SKY'), key=lambda i: i['fcstDate'] + i['fcstTime'], default=None)
    pty = int(float(obs.get('PTY', 0)))
    if pty in (3, 7):
        kind = '눈'
    elif pty:
        kind = '비'
    else:
        kind = '맑음' if sky and sky['fcstValue'] == '1' else '흐림'
    return {'temp': round(float(obs['T1H'])), 'kind': kind}


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body=b'', ctype='application/json', cookie=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        if cookie:
            self.send_header('Set-Cookie', f'uid={cookie}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax')
        self.end_headers()
        self.wfile.write(body)

    def uid(self):
        """쿠키의 익명 사용자 ID. 없거나 형식이 틀리면 새로 발급 → (id, 새로 발급했는지)"""
        for part in self.headers.get('Cookie', '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == 'uid' and len(v) == 32 and v.isalnum():
                return v, False
        return uuid.uuid4().hex, True

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != '/api/state' or not (SB_URL and SB_KEY):
            return self.send(404)
        uid, _ = self.uid()
        try:
            n = int(self.headers.get('Content-Length', 0))
            if n > MAX_STATE:
                return self.send(413)
            data = json.loads(self.rfile.read(n))
            if not isinstance(data, dict) or 'profile' not in data:
                return self.send(400)
            put_state(uid, data)
            self.send(204)
        except ValueError:
            self.send(400)
        except Exception as e:
            print('supabase 저장 실패:', e)
            self.send(502)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == '/api/weather':
            q = urllib.parse.parse_qs(url.query)
            try:
                lat, lon = float(q['lat'][0]), float(q['lon'][0])
                assert KEY
            except (KeyError, ValueError, AssertionError):
                return self.send(400, json.dumps({'error': 'KMA_SERVICE_KEY 또는 좌표 없음'}).encode())
            try:
                self.send(200, json.dumps(weather(lat, lon)).encode())
            except Exception as e:
                print(e)
                self.send(502, json.dumps({'error': str(e)}, ensure_ascii=False).encode())
        elif url.path in ('/', '/index.html'):
            html = (ROOT / 'index.html').read_text(encoding='utf-8')
            uid, new = self.uid()
            if SB_URL and SB_KEY:
                try:
                    remote = json.dumps(get_state(uid)).replace('</', '<\\/')
                except Exception as e:  # Supabase 장애 시 localStorage만으로 동작
                    print('supabase 읽기 실패:', e)
                    remote = 'null'
                html = html.replace('</head>', SHIM.replace('__REMOTE__', remote) + '</head>', 1)
            self.send(200, html.encode(), 'text/html; charset=utf-8', uid if new else None)
        else:
            self.send(404)


if __name__ == '__main__':
    print(f'http://localhost:{PORT}')
    ThreadingHTTPServer(('', PORT), Handler).serve_forever()

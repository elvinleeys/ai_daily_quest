"""정적 파일 + 기상청 단기예보 프록시 + Supabase 상태 저장 API(/api/state) (표준 라이브러리만 사용). 실행: python server.py"""
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


# ---------- Gemini 프록시: 키는 .env(GEMINI_API_KEY)에만 두고 브라우저로 보내지 않는다 ----------
GEMINI_KEY = os.environ.get('GEMINI_API_KEY')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
MAX_AI_BODY = 20_000
CATS = ('health', 'study', 'tidy', 'mind', 'social')
DIFFS = ('easy', 'normal', 'hard')


def gemini(prompt, as_json):
    cfg = {'temperature': 0.9, **({'responseMimeType': 'application/json'} if as_json else {})}
    req = urllib.request.Request(
        f'https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent', method='POST',
        data=json.dumps({'contents': [{'parts': [{'text': prompt}]}], 'generationConfig': cfg}).encode(),
        headers={'Content-Type': 'application/json', 'x-goog-api-key': GEMINI_KEY})
    with urllib.request.urlopen(req, timeout=20) as res:
        data = json.load(res)
    return ''.join(p.get('text', '') for p in data['candidates'][0]['content']['parts']).strip()


def clip(v, n=60):
    return str(v or '')[:n]


def ai_quests(b):
    """프롬프트는 서버에서 만든다 (브라우저가 임의 프롬프트로 키를 쓰지 못하게)"""
    diff = b.get('difficulty') if b.get('difficulty') in DIFFS else 'normal'
    w = b.get('weather') or {}
    h = b.get('history') or {}
    weather_txt = f"{clip(w.get('kind'), 4)}, {clip(w.get('temp'), 4)}도" if w else '모름'
    prompt = f"""너는 일상을 게임 퀘스트로 바꿔 주는 코치야.
조건:
- 오늘 퀘스트 4개를 한국어로 만든다. 각 퀘스트는 30분 안에 끝낼 수 있는 구체적인 행동 하나.
- 난이도 기본값: {diff} (easy/normal/hard). 4개 중 1개는 한 단계 쉬운 것으로 섞는다.
- 오늘 집중 목표: {clip(b.get('goal'), 100) or '없음'} → 최소 2개는 이 목표와 연결한다.
- 날씨: {weather_txt} → 비/눈이면 실내 활동으로.
- 최근 자주 완료한 분야: {', '.join(map(clip, h.get('topCats') or [])[:5]) or '없음'}, 자주 못 한 분야: {', '.join(map(clip, h.get('weakCats') or [])[:5]) or '없음'}
- category는 {', '.join(CATS)} 중 하나.
JSON 배열만 출력: [{{"title":"...","category":"...","difficulty":"easy|normal|hard"}}]"""
    arr = json.loads(gemini(prompt, True))
    quests = [{'title': clip(q.get('title'), 80), 'category': q.get('category'), 'difficulty': q.get('difficulty')}
              for q in arr[:5] if isinstance(q, dict) and q.get('title')]
    if not quests:
        raise ValueError('빈 응답')
    return {'quests': quests}


def ai_comment(b):
    summary = json.dumps(b.get('summary') or {}, ensure_ascii=False)[:4000]
    text = gemini(f'다음은 사용자의 지난 7일 퀘스트 기록이야. 칭찬 1문장 + 다음 주 제안 1문장, 총 2문장으로 따뜻하게 한국어로 써 줘.\n{summary}', False)
    return {'text': text[:500]}


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

    def ai(self, path):
        """POST /api/quests, /api/comment → Gemini. 키가 없으면 503 (브라우저는 기본 퀘스트로 대체)"""
        if not GEMINI_KEY:
            return self.send(503, b'{"error":"GEMINI_API_KEY not set"}')
        try:
            n = int(self.headers.get('Content-Length', 0))
            if n > MAX_AI_BODY:
                return self.send(413)
            body = json.loads(self.rfile.read(n) or '{}')
            if not isinstance(body, dict):
                return self.send(400)
            out = ai_quests(body) if path == '/api/quests' else ai_comment(body)
            self.send(200, json.dumps(out, ensure_ascii=False).encode())
        except Exception as e:
            print('gemini 실패:', e)
            self.send(502)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ('/api/quests', '/api/comment'):
            return self.ai(path)
        if path != '/api/state' or not (SB_URL and SB_KEY):
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
        elif url.path == '/api/state':
            if not (SB_URL and SB_KEY):
                return self.send(503, b'{"error":"SUPABASE_URL/SUPABASE_SERVICE_KEY not set"}')
            uid, _ = self.uid()
            try:
                self.send(200, json.dumps({'data': get_state(uid)}).encode())
            except Exception as e:
                print('supabase 읽기 실패:', e)
                self.send(502)
        elif url.path in ('/', '/index.html'):
            uid, new = self.uid()
            self.send(200, (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8', uid if new else None)
        else:
            self.send(404)


if __name__ == '__main__':
    print(f'http://localhost:{PORT}')
    ThreadingHTTPServer(('', PORT), Handler).serve_forever()

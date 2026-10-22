# AI 데일리 퀘스트 — 기획 · API 정리

> 해야 할 일을 작은 퀘스트로 바꾸고, 완료할 때마다 경험치와 연속 기록을 쌓는 서비스.
> 참고: IRL Life Quest (App Store id6767270211), appgoblin.info/apps/6760564589

- 프로토타입: `index.html` (브라우저로 바로 열면 됨, 설치 없음)
- 범위: **프론트엔드 + 외부 API 연결만**. 자체 백엔드/DB 없음 → 데이터는 브라우저 `localStorage`에 저장.

---

## 1. 사용할 API (모두 무료)

| 용도 | API | 비용 | 키 | 비고 |
|---|---|---|---|---|
| 퀘스트 추천 · 주간 코멘트 | **Google Gemini API** (`gemini-2.5-flash`) | 무료 티어 있음 | 필요 (Google AI Studio에서 발급) | 분당/일일 요청 수 제한 있음. 한도는 AI Studio에서 확인 |
| 날씨 개인화 | **Open-Meteo** | 무료 | **필요 없음** | 비상업용 무료. 상업 서비스면 유료 플랜 확인 |
| 공휴일 | **Nager.Date** (`/api/v3/PublicHolidays/{년}/KR`) | 무료 | **필요 없음** | 한국 기준. 연 1회 받아 `localStorage`에 캐시. 실패하면 평소 난이도 |
| 위치 | 브라우저 Geolocation | 무료 | 없음 | 거부하면 서울 좌표로 대체 |

### 왜 OpenAI가 아니라 Gemini인가
- 의뢰서에는 OpenAI API가 적혀 있지만, **OpenAI API는 무료 티어가 없고 사용량만큼 과금**된다.
- "무료"가 조건이면 Gemini 무료 티어가 가장 현실적. 응답을 JSON으로 강제할 수 있어(`responseMimeType: application/json`) 퀘스트 목록 받기에 적합.
- 대안: **Groq** (무료 티어, OpenAI와 같은 호출 형식) → 나중에 OpenAI로 옮길 계획이면 Groq로 시작하면 코드 변경이 거의 없다.
- 주의: Gemini 무료 티어는 입력 내용이 Google 제품 개선에 쓰일 수 있다. 민감한 개인정보는 프롬프트에 넣지 않는다.

### AI 없이도 동작
- 키가 없거나 호출이 실패하면 **로컬 규칙 기반 퀘스트**(분야 5개 × 난이도 3단계 문장 풀)로 자동 대체.
- 즉 핵심 기능(퀘스트·경험치·연속 기록·리포트)은 외부 API 0개로도 돌아간다.

---

## 2. 하루 3번 들어올 이유 → 화면 구성

| 시간대 | 탭 | 하는 일 |
|---|---|---|
| 아침 (~11시) | 🌅 아침 | 어제 정한 목표 확인 → 오늘 집중할 것·난이도 고르기 → **오늘의 퀘스트 받기** |
| 낮 (11~19시) | ⚔️ 퀘스트 | 퀘스트 체크 → 즉시 XP 획득 + 주간 보스에게 피해, 진행률 확인, 마음에 안 드는 퀘스트 ↻ 교체 |
| 밤 (19시~) | 🌙 밤 | 오늘 결과 요약 → **보상 상자 열기**(보너스 XP) → 내일 목표 저장 |
| 언제나 | 📊 리포트 | 최근 7일 달성률 막대, 분야별 완료 수, 한 줄 평(AI) |

현재 시간대에 맞는 탭이 자동으로 열리고 "지금" 표시가 붙는다.

---

## 3. 차별점 → 구현 방식

| 차별점 | 구현 |
|---|---|
| 목표 난이도 조절 | 쉬움/보통/어려움 선택. **최근 3일 달성률 90% 이상 → 한 단계 올리기 제안, 50% 미만 → 내리기 제안**. 퀘스트 4개 중 1개는 한 단계 쉬운 것으로 섞어 실패감 줄임 |
| 공휴일 난이도 완화 | 오늘이 한국 공휴일이면 퀘스트를 **설정 난이도보다 한 단계 쉽게** 생성(어려움→보통, 보통→쉬움, 쉬움→쉬움). 설정값은 바뀌지 않아 다음 날 자동 복귀. 상단에 🎌 공휴일 이름 표시. 주말은 해당 없음 |
| 연속 기록 | 하루에 1개 이상 완료하면 이어짐. 하루 건너뛰면 0으로 |
| 주간 리포트 | 최근 7일 일별 달성률, 완료 수, 얻은 XP, 분야별 완료 수 + AI 한 줄 평 |
| 개인별 퀘스트 추천 | AI 프롬프트에 오늘 목표·난이도·날씨·자주 한 분야/못 한 분야를 넣어 생성. 비/눈이면 실내 운동으로 |

### 게임 규칙
- XP: 쉬움 10 / 보통 20 / 어려움 35
- 레벨업 필요 XP: `100 + (레벨-1) × 50`
- **콤보**: 30분 안에 연달아 완료하면 XP 배율 +10% (최대 1.5배)
- **주간 보스**: 매주 월요일 새 보스(게으름 슬라임 → 미루기 유령 → 알림 골렘 순환). HP `300 + (레벨-1) × 25`, 퀘스트로 얻은 XP만큼 피해. 처치하면 +50 XP
- **캐릭터 진화**: Lv.1 견습 모험가 → Lv.3 떠돌이 전사(머리띠) → Lv.5 숙련 기사(검·갑옷) → Lv.8 왕국의 수호자(투구·망토) → Lv.12 전설의 영웅(왕관·황금 갑옷)
- 밤 보상 상자: 전부 완료 +30 XP, 절반 이상 +10 XP (하루 1번)
- 퀘스트 분야: 건강 · 공부 · 정리 · 마음 · 관계

### 일러스트 · 인터랙션 (레퍼런스: Questify - Task RPG)
레퍼런스의 분위기(어두운 배경 + 금색 포인트, 해 질 녘 성과 떠오르는 등불, 동그란 초상화 + 레벨 배지, 레벨업 전체 화면)만 참고했고, 그림은 모두 **직접 그린 SVG**다. 이미지 파일이 없어서 외부 저작권 문제가 없다.

| 요소 | 동작 |
|---|---|
| 배경 장면 | 성·산·나무 일러스트. 탭(아침/낮/밤)에 따라 하늘·해·달·별·등불·구름이 1.4초 동안 바뀜 |
| 날씨 | Open-Meteo 결과가 비면 빗줄기, 눈이면 눈송이가 배경에 내림 |
| 캐릭터 | 숨 쉬듯 위아래로 움직임. 누르면 점프 + 말풍선. 레벨에 따라 장비가 바뀜 |
| 퀘스트 완료 | 반짝이 터짐 → `+XP` 숫자 → 빛 구슬이 XP 바로 날아감 → 보스가 맞고 흔들림 + 피해 숫자 |
| 퀘스트 받기 | 카드가 한 장씩 날아와 깔림 |
| 레벨업 · 진화 · 보스 처치 | 회전하는 빛살 + 색종이가 있는 전체 화면 |
| 밤 보상 | 상자가 흔들리다가 누르면 뚜껑이 열리며 보상 |
| 리포트 | 진화 단계 5개. 아직 못 간 단계는 그림자로 가려짐 |

- 움직임 줄이기 설정(`prefers-reduced-motion`)을 켠 사용자는 애니메이션 없이 보여 준다.

---

## 4. 프론트엔드 ↔ API 연결 명세

프로토타입의 `api` 객체 하나에 외부 호출을 모아 두었다. 화면 코드는 이 세 함수만 부른다.
**실서비스로 갈 때는 이 객체 안쪽만 바꾸면 된다.**

### `api.generateQuests(input) → { source, quests }`
```js
// input
{ goal: "토익 공부", difficulty: "normal", weather: { kind: "비", temp: 14 },
  history: { topCats: ["공부"], weakCats: ["관계", "정리"] } }
// output
{ source: "ai" | "local" | "local-fallback",
  quests: [{ id, title, category: "health|study|tidy|mind|social",
             difficulty: "easy|normal|hard", xp, done: false }] }
```
- 내부: `POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent`
  - 헤더 `x-goog-api-key: <키>`, `generationConfig.responseMimeType = "application/json"`
  - 응답 `candidates[0].content.parts[0].text` 를 JSON 파싱

### `api.getWeather(lat, lon) → { temp, code, kind }`
- 내부: `GET https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&current=temperature_2m,weather_code&timezone=auto`
- `kind`: 맑음 / 흐림 / 비 / 눈 (WMO weather_code로 분류)

### `api.weeklyComment(summary) → string`
- 입력: `{ done, total, xp, rate, categories:[{cat,count}], days:[{date,rate}] }`
- AI 실패 시 달성률 구간별 기본 문장으로 대체

### 저장 데이터 (`localStorage["dailyQuest_v1"]`)
```js
{ profile: { xp, level, streak, lastDoneDate, difficulty },
  days: { "2026-10-22": { quests: [...], claimed, bonus, tomorrowGoal } },
  settings: { apiKey, model } }
```

---

## 5. 실서비스 전 꼭 바꿀 것

1. **API 키를 브라우저에 두지 않는다.** 프로토타입은 사용자가 자기 키를 넣는 방식이라 괜찮지만, 서비스 키를 프론트에 넣으면 누구나 가져다 쓸 수 있다.
   → 무료로 하려면 **Cloudflare Workers**(무료 플랜) 같은 서버리스 함수 하나를 프록시로 두고, `api.generateQuests`가 그 주소를 부르게 바꾼다.
2. 여러 기기 동기화가 필요하면 `localStorage` → Supabase/Firebase 무료 플랜으로 교체 (명세의 저장 구조 그대로 사용 가능).
3. 아침/밤 알림은 웹에서는 PWA 푸시가 필요 → 2단계 범위로 둔다.

---

## 6. 프로토타입 확인 방법

1. `index.html`을 브라우저로 연다.
2. 오른쪽 위 ⚙️ → **7일 샘플 데이터**를 누르면 리포트·연속 기록·난이도 제안을 바로 볼 수 있다. **+150 XP 체험**을 누르면 레벨업·진화 화면을 볼 수 있다.
3. 아침 탭 → 퀘스트 받기 → 퀘스트 탭에서 ⚔ 체크 → 밤 탭에서 보상 상자 열기.
4. (선택) https://aistudio.google.com 에서 무료 키 발급 → 설정에 넣으면 AI 맞춤 퀘스트로 바뀐다.

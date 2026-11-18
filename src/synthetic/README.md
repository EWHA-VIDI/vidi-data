# 합성 군집 생성기

탐지 파이프라인의 탐지율을 재기 위해, 정답을 아는 "조직적 바이럴" 군집을 만든다.

## 실행

```bash
pip install -r requirements.txt
python -m src.synthetic --out data/synthetic/run01            # 전부 합성
python -m src.synthetic --inject-dir data/raw/run01 --out data/synthetic/inject01   # 기존 표에 군집만 심기
pytest
```

계정 ID를 가명화하려면 `VIDI_HASH_KEY` 환경 변수를 설정한다 (HMAC-SHA256, 64자). 키는 커밋하지 않는다.

## 출력

| 파일 | 내용 |
|---|---|
| `videos.csv` | video_id, channel_id, title, uploaded_at |
| `channels.csv` | channel_id, channel_created_at, subscriber_count |
| `comments.csv` | comment_id, video_id, author_channel_id, parent_comment_id, content, like_count, written_at |
| `labels.json` | `clusters`(심은 군집 정답), `decoys`(유명 제품 칭찬 묶음, 오탐 함정), 생성 설정 |

- 시간은 UTC ISO 8601 (`2026-10-01T03:00:00Z`)
- 정답은 `labels.json`에만 있다. 탐지 코드는 CSV만 읽는다.
- 컬럼 이름은 공통 스키마(VIDI-45) 확정 후 `Config.columns`로 맞춘다.

## 군집 설계

| 난이도 | 계정 | 영상 | 업로드 후 | 신규 계정 | 문장 변형 | 맞장구 답글 |
|---|---|---|---|---|---|---|
| easy | 8–15 | 4–8 | 0.2–3시간 | 90% | 낮음 | 30% |
| medium | 5–10 | 3–6 | 0.5–12시간 | 60% | 중간 | 15% |
| hard | 3–6 | 2–4 | 1–48시간 | 30% | 높음 | 5% |

- 심는 제품: 제품 사전 `awareness_tier`가 중간·저언급인 제품
- 일반 댓글의 제품 언급 비율: 유명 5 : 중간 1 : 저언급 0.2
- 문장: "고민 → 제품 → 개선 → 권유" 구조. 고민은 제품 사전 `concerns`, 사용 표현은 `category`를 따른다.
- 제품 표기: 정식명·별칭·브랜드 별칭을 섞고, `needs_brand` 별칭은 브랜드를 붙인다.

## 주의

- 공개 자료(발표·포스터·데모)에는 `generate()`로 만든 전부 합성 데이터만 쓴다.
- `inject()`로 실제 데이터에 심은 결과는 내부 평가용이며, Researcher Program 승인 후에만 실제 데이터에 쓴다.

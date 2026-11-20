# 합성 군집 생성기

탐지 파이프라인의 탐지율을 재기 위해, 정답을 아는 "조직적 바이럴" 군집을 만든다.

## 실행

```bash
pip install -r requirements.txt
python -m src.synthetic --out data/synthetic/run01                 # 전부 합성
python -m src.synthetic --out data/synthetic/run01 --csv           # 검수용 CSV도 저장
python -m src.synthetic --inject-dir data/collected/schema_v1/<run>/shard_03 --out data/synthetic/inject01   # 기존 표에 군집만 심기
pytest
```

## 출력 (공통 규격 v1)

| 파일 | 내용 |
|---|---|
| `videos.parquet` | video_id, channel_id, title, description, published_at, duration_seconds, view_count, like_count, comment_count, collected_at |
| `channels.parquet` | channel_id, published_at, subscriber_count, video_count, view_count, collected_at |
| `comments.parquet` | comment_id, video_id, author_channel_id, parent_comment_id, text, published_at, updated_at, like_count, reply_count, collected_at |
| `manifest.json` | schema_version, run_id, synthetic, pseudonym_key_version, collected_at, row_counts |
| `labels.json` | `clusters`(심은 군집 정답), `decoys`(유명 제품 칭찬 묶음, 오탐 함정), 생성 설정 |

- Parquet(zstd), 시간은 `timestamp[us, UTC]`. `--csv`를 주면 같은 이름의 CSV도 저장한다 (시간은 `...Z` 표기).
- 답글: `parent_comment_id`로 최상위 댓글 연결. 최상위 댓글의 `reply_count`는 답글 수, 답글 행은 null.
- ID: 영상 ID는 원문, 채널·댓글 ID는 `ch_`/`cm_` + HMAC-SHA256(키, `channel:`/`comment:` + 원본) 64자.
  - 키는 환경 변수 `DATA_PSEUDONYM_KEY`. 없으면 합성 전용 키를 쓰고 manifest에 `pseudonym_key_version: synthetic`으로 남긴다.
  - `inject()`에서 이미 가명화된 ID(`ch_`, `cm_`)는 다시 바꾸지 않는다. 실제 데이터에 심을 때는 수집과 같은 키를 준다.
- 정답은 `labels.json`에만 있다. 탐지 코드는 데이터 파일만 읽는다.

## 군집 설계

| 난이도 | 계정 | 영상 | 업로드 후 | 신규 계정 | 문장 변형 | 맞장구 답글 |
|---|---|---|---|---|---|---|
| easy | 8–15 | 4–8 | 0.2–3시간 | 90% | 낮음 | 30% |
| medium | 5–10 | 3–6 | 0.5–12시간 | 60% | 중간 | 15% |
| hard | 3–6 | 2–4 | 1–48시간 | 30% | 높음 | 5% |

- 심는 제품: 제품 사전 `awareness_tier`가 중간·저언급인 active 제품. 없으면 에러 (유명 제품으로 대신하지 않음)
- 계정: 군집 계정과 일반 계정의 구독자 수 분포가 같다. 일반 계정의 10%는 최근(3~60일) 생성 계정이라 계정 나이만으로는 군집이 드러나지 않는다
- 일반 댓글의 제품 언급 비율: 유명 5 : 중간 1 : 저언급 0.2
- 문장: "고민 → 제품 → 개선 → 권유" 구조. 고민은 제품 사전 `concerns`, 사용 표현은 `category`를 따른다.
- 제품 표기: 정식명·별칭·브랜드 별칭을 섞고, `needs_brand` 별칭은 브랜드를 붙인다.

## 주의

- 공개 자료(발표·포스터·데모)에는 `generate()`로 만든 전부 합성 데이터만 쓴다.
- `inject()`로 실제 데이터에 심은 결과는 내부 평가용이며, Researcher Program 승인 후에만 실제 데이터에 쓴다.

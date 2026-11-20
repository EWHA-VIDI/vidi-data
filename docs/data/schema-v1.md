# 수집 공통 스키마 v1

담당: 박지연 · VIDI-45 · 기준일: 2026-11-19

## 공통

| 항목 | 기준 |
| --- | --- |
| 공식 파일 | videos.parquet / channels.parquet / comments.parquet, zstd |
| ID·본문 | UTF-8 string |
| 개수 | int64, 실제 0과 null을 구분 |
| 시간 | timestamp[us, UTC], JSON·CSV는 UTC ISO 8601 `2026-11-18T03:00:00Z` |
| null | 아래 nullable=예인 컬럼만 허용 |
| 수집 시각 | 각 행의 collected_at, API 응답을 받은 시각 |
| 샤드·실행 | 행에 반복하지 않고 파일 경로와 manifest에 저장 |

정의의 실행 가능한 기준은 `src/collection/schema.py`의 SCHEMAS이다. 컬럼 이름·순서·타입·null 허용 여부를 검사한다. 원본 식별자나 author_name 같은 추가 컬럼을 파일에 끼워 넣지 않는다.

## videos — 영상 1개당 1행

| 컬럼 | 타입 | nullable | 의미·API 출처 |
| --- | --- | --- | --- |
| video_id | string | 아니오 | videos.id 원문, 숫자로 변환하지 않음 |
| channel_id | string | 아니오 | snippet.channelId의 공통 가명 ID |
| title | string | 아니오 | snippet.title |
| description | string | 예 | snippet.description |
| published_at | timestamp | 아니오 | snippet.publishedAt, 시간 신호의 기준 |
| duration_seconds | int64 | 예 | contentDetails.duration을 초로 변환한 값 |
| view_count | int64 | 예 | statistics.viewCount |
| like_count | int64 | 예 | statistics.likeCount |
| comment_count | int64 | 예 | statistics.commentCount, API 전체 수 |
| collected_at | timestamp | 아니오 | 이 행의 정보 조회 시각 |

comment_count는 수집한 comments 행 수와 같다고 가정하지 않는다. 쇼츠를 길이로 제외하지 않는다. 180초 이하를 표시하는 short_like는 분석용 마스터 메타데이터이며 확정 쇼츠 판정이 아니다.

## channels — 채널 1개당 1행

업로더와 댓글 작성자를 같은 표에 넣는다. 같은 원본 채널이면 역할과 영상에 관계없이 같은 가명 ID를 쓴다.

| 컬럼 | 타입 | nullable | 의미·API 출처 |
| --- | --- | --- | --- |
| channel_id | string | 아니오 | channels.id의 공통 가명 ID |
| published_at | timestamp | 예 | snippet.publishedAt, 채널 생성일 |
| subscriber_count | int64 | 예 | statistics.subscriberCount, 공개·반올림된 값 |
| video_count | int64 | 예 | statistics.videoCount, 공개 영상 수 |
| view_count | int64 | 예 | statistics.viewCount |
| collected_at | timestamp | 아니오 | 이 행의 정보 조회 시각 |

- hiddenSubscriberCount=true이거나 공개 여부를 확인하지 못하면 구독자 수는 null이다. 공개된 0명만 0으로 쓴다.
- API가 삭제·조회 불가 채널을 반환하지 않으면 ID만 있는 채널 행을 만들고 메타데이터는 null로 둔다. 생성일을 댓글 시각으로 추정하지 않는다.
- 작성자 원본 ID 자체가 없으면 comments.author_channel_id=null이고 가짜 채널 행을 만들지 않는다.
- 표시명, 핸들, 프로필 이미지, 채널 URL은 저장하지 않는다.
- 생성일이 없으면 신규 계정 신호는 확인 불가이다. 생성일은 실제 활동 시작일과 같다고 가정하지 않으며 A3는 보조 메모에만 쓴다.

## comments — 댓글·답글 1개당 1행

| 컬럼 | 타입 | nullable | 의미·API 출처 |
| --- | --- | --- | --- |
| comment_id | string | 아니오 | comments.id의 공통 가명 ID |
| video_id | string | 아니오 | snippet.videoId, videos 참조 |
| author_channel_id | string | 예 | snippet.authorChannelId.value의 공통 가명 ID |
| parent_comment_id | string | 예 | snippet.parentId의 공통 가명 댓글 ID |
| text | string | 아니오 | textFormat=plainText로 요청한 snippet.textDisplay |
| published_at | timestamp | 아니오 | snippet.publishedAt |
| updated_at | timestamp | 예 | snippet.updatedAt, 없으면 null |
| like_count | int64 | 예 | snippet.likeCount |
| reply_count | int64 | 예 | 최상위 댓글은 thread.snippet.totalReplyCount, 답글은 null |
| collected_at | timestamp | 아니오 | 해당 댓글 응답을 받은 시각 |

- 공개 조회에서 textOriginal에 의존하지 않는다. textDisplay를 이모지·공백·줄바꿈 그대로 저장하고 정규화는 후처리에서 한다.
- 최상위 댓글의 parent_comment_id는 null, 답글은 최상위 댓글의 comment_id를 넣는다. is_reply는 중복 저장하지 않는다.
- reply_count는 받은 답글 행 수로 덮어쓰지 않는다. 실제 전체 수를 모르면 null이다.
- commentThreads의 포함 답글만으로 전체 수집을 완료했다고 판단하지 않는다. totalReplyCount보다 적으면 comments.list로 나머지 페이지를 받는다.
- 댓글·답글·운영자 댓글을 수집 단계에서 후기 여부로 거르지 않는다. 운영자 여부는 author_channel_id == videos.channel_id로 후처리한다.
- 광고 표시·이벤트·의약품·패치 언급도 수집은 한다. 분석 제외·제품 매칭은 최신 01·02·03 문서를 따른다.

## ID와 날짜 검증

VIDI-44의 `ch_`/`cm_` + HMAC-SHA256 소문자 hex 64자 규칙을 쓴다. 키 버전이 같아야 병합하며 HMAC 키를 manifest에 넣지 않는다. 내부 가명 ID를 화면·외부 CSV에 노출하지 않는다.

시간대 없는 datetime을 거부하고 명시적인 offset은 UTC로 변환한다. 다음 조건을 검사한다.

- 게시 시각 ≤ collected_at
- 댓글 게시 시각 ≤ updated_at ≤ collected_at (수정 시각이 있을 때)
- 영상 게시 시각 ≤ 댓글 게시 시각
- 부모 댓글 게시 시각 ≤ 답글 게시 시각

## API 응답과 연결

`YouTubeRows`는 이미 받은 YouTube API 리소스를 행으로 바꾸는 출력 경계다. API 키·네트워크 호출·검색·페이지네이션·할당량 처리는 담당 수집기에 둔다. VIDI-44 PR을 먼저 머지한 뒤 공통 Pseudonymizer를 전달한다.

```python
from src.collection.api_rows import YouTubeRows
from src.collection.pseudonyms import Pseudonymizer  # VIDI-44
from src.collection.schema import table_from_rows

ids = Pseudonymizer.from_environment()
mapper = YouTubeRows(ids, collected_at="2026-11-20T03:20:00Z")
# resource는 API에서 이미 받은 단일 comment 리소스다.
row = mapper.comment(resource, total_reply_count=thread_total_reply_count)
comments = table_from_rows("comments", [row])
```

원본 ID로 channels.list를 조회하는 단계는 디스크 저장 전에 끝낸다. 채널 조회는 팀 결정대로 50개 단위로 나누되 이를 API 문서의 ID 개수 상한이라고 단정하지 않는다. 조회·변환이 끝난 원본 응답과 원본-ID 매핑은 파일·로그로 남기지 않는다.

## 병합 기준

| 표 | 중복 키 | 선택 |
| --- | --- | --- |
| videos | video_id | collected_at이 최신인 행 전체 |
| channels | channel_id | collected_at이 최신인 행 전체 |
| comments | comment_id | collected_at이 최신인 행 전체 |

같은 영상이 두 샤드에 있으면 더 늦게 수집한 쪽의 영상·댓글 묶음을 택하고 선택 내역을 남긴다. 이후 comment_id로 중복을 재확인한다. 서로 다른 수집 시점의 컬럼을 한 행으로 섞지 않는다. 동일 시각의 동일 ID에 다른 내용이 있으면 충돌로 기록하고 확인 후 병합한다. 분석용 같은 계정·같은 영상·같은 문장의 중복 계산은 원본 comment_id 중복 제거와 별도 단계다.

완전 수집은 부모 댓글이 같은 영상에 존재해야 한다. 부분 수집에서 부모가 없으면 오류를 숨기지 않고 partial과 missing_parent_count로 기록한다. 업로더·알려진 작성자 채널의 참조 행은 부분 수집에서도 필요하다.

## 저장과 manifest

`write_collection()`은 스키마와 참조를 확인하고 임시 디렉터리에서 Parquet 3종과 manifest를 만든 뒤 최종 경로에 옮긴다. 완료 경로를 덮어쓰지 않는다. 저장·S3 경로 결정은 VIDI-43을 따른다.

| 필드 | 의미 |
| --- | --- |
| kind | collection, 백엔드 분석 적재 manifest와 구분 |
| schema_version | v1 |
| run_id / shard_id | run_YYYYMMDDTHHMMSSZ / shard_01·02·03 또는 merged |
| source_kind | youtube 또는 synthetic, 합성 데이터와 실제 수집 출처 구분 |
| pseudonym_key_version | 키 버전만 기록 |
| started_at / finished_at | 실행 시작·종료 UTC 시각 |
| collected_at_min / collected_at_max | 실제 행의 가장 이른·늦은 수집 시각, 빈 샤드는 null |
| expires_at | 가장 이른 수집 시각 + 30일, 빈 샤드는 실행 시작 + 30일 |
| collection_status | complete 또는 partial |
| failed_video_ids | 실패한 영상 ID 목록, 실패가 있으면 complete 불가 |
| row_counts | videos / channels / comments의 실제 행 수 |
| quality.missing_parent_count | 누락된 부모 댓글 참조 수 |
| files | Parquet 파일별 SHA-256, 업로드 후 무결성 확인에 사용 |

합성 생성기 #3의 기존 manifest는 별도 구현이다. 이 문서는 공통 수집 manifest를 추가하며 다른 담당자의 합성 코드를 임의 수정하지 않는다. #3의 연결은 이 공통 규격 기준으로 별도 리뷰한다.

## 실행·검증

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_collection_schema.py' -v
```

테스트는 가짜 API 응답으로 변환 경계와 Parquet 저장·재읽기를 확인한다. 실제 API 호출 성공·팀원 로컬 실행·팀원 리뷰 완료를 뜻하지 않는다.

## 기준 문서

- 제품명세서 ANA-02, ANA-04, COM-04·05, 9장 데이터
- [01. 수집 범위](https://app.notion.com/p/01-41bb674b662180b18b51f5dbe3a51cd5): 전체 댓글·답글 수집, 마스터 목록·샤드·저장 형식
- [02. 제품 사전](https://app.notion.com/p/02-41bb674b662180849074de0118cfef9e): 아이크림 cream 유지, spot 추가 없음, 제품 매칭은 후처리
- [03. 검수 기준](https://app.notion.com/p/03-41bb674b6621809ea43cd9e5ddce34f8): 계정 연결, 부모 댓글 맥락, 생성일 보조 신호

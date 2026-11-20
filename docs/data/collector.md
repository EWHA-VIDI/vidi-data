# 공통 댓글 수집기

담당: 박지연 · VIDI-45 · Python 3.12 / Linux·Colab

동결된 마스터 영상 목록의 담당 샤드만 수집한다. 검색·영상 선별·샤드 재배정은 수행하지 않는다. 공개 댓글과 답글을 모두 받고 후기·광고 표시·제품 언급에 따른 필터는 후처리에 둔다.

## 선행 조건

1. VIDI-44(#5)의 `src/collection/pseudonyms.py`와 VIDI-45(#6)의 스키마·본체 적용
2. 팀 공통 DATA_PSEUDONYM_KEY(32바이트 이상)와 DATA_PSEUDONYM_KEY_VERSION 주입
3. 본인 프로젝트의 YOUTUBE_API_KEY 주입; 키는 채팅·Git·노트북 출력에 쓰지 않음
4. 동결된 마스터 JSON 배열 준비; 실제 API 실행·정책 승인 범위는 팀 결정 준수

```json
[
  {"video_id": "abcdefghijk", "shard": 1, "comment_count": 100},
  {"video_id": "12345678901", "shard": "shard_02", "commentCount": 5000}
]
```

ID는 실제 마스터 영상 ID로 대체한다. shard는 1·2·3 또는 shard_01·02·03이다. 다른 마스터 메타데이터가 있어도 보존 파일에 복사하지 않는다. 중복 영상·잘못된 배정은 실행 전에 거부한다. 댓글 수 5,000개 이상 영상은 자기 샤드의 마지막으로 미루며 예산이 남으면 수집한다. 길이·쇼츠·광고 표시로 수집 목록을 임의로 바꾸지 않는다.

## 실행

```bash
python -m pip install -r requirements.txt
python -m src.collection --help
python -m src.collection \
  --master data/master_videos.json \
  --shard shard_01 \
  --run-id run_20261120T030000Z \
  --work data/checkpoints/run_20261120T030000Z/shard_01 \
  --out data/collected/schema_v1/run_20261120T030000Z/shard_01 \
  --budget-file data/budgets/project_jiyeon.json
```

키는 위 명령에 직접 넣지 않고 권한 있는 환경에서 환경변수로 주입한다. 시드·예시 키로 실제 수집하지 않는다. 예산 파일 이름은 키가 아니라 프로젝트를 식별하며 같은 프로젝트를 쓰는 모든 프로세스가 같은 파일을 공유한다.

- commentThreads.list: order=time, maxResults=100, textFormat=plainText
- comments.list: 포함 답글이 부족한 스레드의 모든 페이지, maxResults=100, textFormat=plainText
- 빈 페이지·100개 미만 응답이어도 nextPageToken이 있으면 계속 조회; 반복 토큰은 오류
- thread.id 대신 topLevelComment.id로 부모 연결; 댓글 ID 중복 제거
- channels.list: 페이지 작성자들을 50개 단위로 조회하고 업로더도 같은 channels 표에 저장
- 조회되지 않는 채널은 가명 ID만 남기고 생성일·통계는 null; 공개 구독자 0과 비공개 구분
- 원본 ID는 채널 조회에만 메모리에서 사용하며 파일에는 HMAC ID만 저장
- 출력: Parquet(zstd) 3종 + 수집 manifest, 백엔드 분석 완료 manifest는 생성하지 않음

## 호출 예산·오류

기본 하루 예산은 8,000회이며 사용 API의 각 호출·실패·재시도를 1회로 계산한다. search.list는 호출하지 않는다. 예산 파일은 태평양 시간 자정을 기준으로 초기화하고 잠금으로 동시 사용을 보호한다. API에서 일일 할당량 오류가 오면 즉시 중단한다.

**로컬 파일은 이 수집기를 통하지 않은 API 호출까지 알 수 없다.** Google Cloud의 프로젝트 사용량을 먼저 확인하고, 이미 사용한 양과 예비량을 반영해 `--daily-budget`을 더 작게 설정한다. 다른 기기에서 같은 프로젝트를 쓰면 중앙 예산 관리를 별도로 해야 한다. 키만 바꿔 카운터를 새로 시작하지 않는다.

429·일시적 rate limit·5xx·네트워크 오류는 1·2·4초 대기 후 총 4회까지 시도하며 각 요청 timeout은 30초이다. 인증 실패는 반복하지 않고 실행을 중단한다. commentsDisabled·삭제 영상은 원본 응답 없이 실패 사유만 기록하고 다음 영상으로 진행한다. 전체 답글 수보다 실제 반환 개수가 적으면 완전 수집으로 표시하지 않는다. API 데이터는 수집 중 변할 수 있으며 시점 고정 스냅샷을 보장하지 않는다.

## 중단 후 재개

- 완료 영상마다 가명화된 Parquet·manifest를 work/videos/<video_id>/에 원자적으로 저장
- 재실행하면 완료 영상은 checksum·스키마·키 버전·기한을 확인하고 API를 재호출하지 않음
- 수집 중이던 영상은 원본 ID나 페이지 토큰을 저장하지 않고 영상 처음부터 다시 조회
- 같은 work에서 마스터·run·shard·키·키 버전이 바뀌면 중단; 같은 work의 동시 실행도 차단
- 완료된 출력 폴더는 덮어쓰지 않음; partial 결과가 있으면 같은 work와 **새로운 out 경로**로 재개
- 종료 코드 0은 complete, 2는 partial 저장, 1은 설정·입력·체크포인트 오류
- 실행을 완전히 강제 종료해 partial 출력이 없어도 영상 체크포인트로 재개 가능

```bash
# 나머지 옵션은 위 실행과 같게 유지하고 out만 새 경로로 지정
# --work data/checkpoints/run_20261120T030000Z/shard_01
# --out data/collected/schema_v1/run_20261120T030000Z/shard_01_retry01
```

최종 채택할 결과를 팀 마스터/병합 기록에 남긴다. 체크포인트도 API 데이터 복사본이므로 30일 내 갱신·삭제하며, 만료된 체크포인트는 재사용을 막는다. 갱신은 새 run/work에서 실제 API를 다시 조회한다. state.json에는 마스터 해시·키 버전·가명 키 확인값·안전한 오류 코드만 저장하며 키 자체·계정 원본 ID·표시명·응답 JSON은 저장하지 않는다.

## S3 공유

업로드는 수집과 분리한다. 실제 비공개 버킷·IAM 설정 후 VIDI-43 경로로 공유하며 파일 3종과 checksum을 확인한 뒤 샤드 manifest.json을 마지막에 업로드한다. 수집기가 임의로 클라우드에 업로드하거나 공개 권한을 만들지 않는다. 원본·partial·체크포인트·검수 CSV 모두 보관·삭제 대상에 포함한다.

## 검증

```bash
python -m unittest discover -s tests -v
```

가짜 API로 다중 페이지·답글 누락 보충·배치 조회·예산·재개·가명 출력·Parquet 재읽기를 검증한다. 이 테스트가 실제 API 인증·네트워크 연결·전체 데이터 수집 성공을 뜻하지 않는다.

# 수집 데이터 저장·공유 규칙

담당: 박지연 · VIDI-43 · 기준일: 2026-11-19

## 결정

- 공식 원본은 Parquet, 압축은 Zstandard(`zstd`)로 저장한다.
- 시간은 `timestamp[us, UTC]`, 정수는 int64, ID는 문자열로 저장한다. 값이 없으면 null로 둔다.
- CSV는 필요한 일부 행의 내부 검수·내보내기에만 사용한다. 원본과 같은 보관 기한을 적용한다.
- CSV는 UTF-8, 쉼표 구분, 헤더 포함, LF로 저장한다. 쉼표·따옴표·줄바꿈이 있는 필드는 큰따옴표로 감싸고 내부 큰따옴표는 두 번 쓴다. `csv` 모듈을 사용하며 문자열을 직접 이어 붙이지 않는다.
- CSV null은 빈 셀이다. CSV만으로 빈 문자열과 null을 복원하지 않고 Parquet을 기준으로 한다.
- 화면·외부 CSV에는 내부 가명 채널 ID도 내보내지 않는다. 공개용 익명 계정 표시는 별도 매핑을 사용한다.

## 경로와 이름

공통 run_id는 수집 실행 시작 시각으로 만든 `run_YYYYMMDDTHHMMSSZ`이다. 세 샤드가 같은 run_id를 공유한다. 별도 재수집 실행에는 새 run_id를 발급한다.

```text
data/collected/schema_v1/<run_id>/
├── shard_01/                   # 지연: 여드름
│   ├── videos.parquet
│   ├── channels.parquet
│   ├── comments.parquet
│   └── manifest.json
├── shard_02/                   # 서현: 비립종 + 좁쌀, 동일한 파일 4종
└── shard_03/                   # 미리: 기준선, 동일한 파일 4종

data/merged/schema_v1/<run_id>/ # 병합 후 동일한 파일 4종
```

- 영상 배정은 11/19 동결된 마스터 목록을 따른다. 기준선 → 비립종·좁쌀 → 여드름 순서로 각 샤드 조건을 확인하며, 최초 통과한 샤드만 수집한다.
- 수집은 `order=time`, `maxResults=100`, `textFormat=plainText`를 사용한다. 댓글·답글을 전부 수집하고 후기·광고 표시 필터는 후처리에서 적용한다.
- 시간·ID·컬럼의 상세는 VIDI-44 가명 처리 결정과 VIDI-45 공통 스키마를 따른다.

## 공유 위치

제품명세서의 비공개 S3 버킷 하나를 사용한다. 실제 버킷 이름은 배포 설정의 `VIDI_DATA_BUCKET`으로 전달하며 코드나 문서에 임의의 버킷을 만들지 않는다.

```text
s3://${VIDI_DATA_BUCKET}/runs/<run_id>/raw/schema_v1/shard_01/<파일 이름>
s3://${VIDI_DATA_BUCKET}/runs/<run_id>/raw/schema_v1/shard_02/<파일 이름>
s3://${VIDI_DATA_BUCKET}/runs/<run_id>/raw/schema_v1/shard_03/<파일 이름>
s3://${VIDI_DATA_BUCKET}/runs/<run_id>/merged/schema_v1/<파일 이름>
```

- 원본 접근은 수집·병합 담당 팀원에게만 허용한다. 공개 URL, 메신저 파일 첨부, Git 커밋으로 주고받지 않는다.
- S3 Block Public Access를 유지하고 팀 IAM 권한으로 접근한다. signup-docs/의 소속 확인 서류와 섞지 않는다.
- 파일 3개를 업로드하고 검사한 뒤 샤드 manifest.json을 마지막에 올린다. 중단된 업로드를 완료 데이터로 사용하지 않는다.
- 샤드·병합 manifest는 `kind=collection`이다. `runs/<run_id>/manifest.json`은 분석 완료 후 백엔드 적재를 위한 별도 계약이므로 수집기가 만들지 않는다.
- 실제 버킷 생성·IAM 권한 부여·환경변수 등록은 인프라 작업이다. 이 문서는 경로 계약을 정하며 등록 완료를 뜻하지 않는다.

## 재시도·재수집·병합

- 완료된 run 폴더는 덮어쓰지 않는다. 재수집은 새 run에 저장한다.
- 같은 실행의 실패 재시도는 아직 완료 manifest가 없는 샤드에서 수행한다. 모든 페이지의 댓글을 합치고 comment_id 중복을 제거한 뒤 최종 파일을 저장한다.
- `.partial` 같은 임시 경로에서 파일을 완성하고 검사 후 최종 디렉터리에 옮긴다. manifest의 `collection_status`로 complete/partial을 구분한다.
- 영상은 video_id, 채널은 channel_id, 댓글은 comment_id로 중복을 제거한다. 같은 키는 collected_at이 최신인 행 전체를 선택한다.
- 같은 영상이 두 샤드에 있으면 늦게 수집한 샤드를 채택하고 선택한 샤드와 제외한 샤드를 병합 기록에 남긴다. 이미 정한 수집 범위를 중복으로 넓히지 않는다.
- 미조회·실패 영상, 일부 답글 누락, 부모 댓글 누락을 기록한다. 부분 수집을 전체 수집으로 표시하지 않는다.

## 30일 보관

- 각 행의 collected_at과 manifest의 collected_at_min, expires_at으로 갱신·삭제 대상을 찾는다.
- expires_at은 포함한 API 데이터 중 가장 오래된 수집 시각 + 30일이다. 파일 복사·병합·가명화·분석만으로 기한을 연장하지 않는다.
- 댓글 본문과 API 통계값(댓글 수·조회수·구독자 수 등)은 30일을 넘겨 그대로 보관하지 않는다.
- 갱신은 같은 마스터 영상 목록으로 API를 다시 조회하는 것이다. 검색을 다시 하거나 기존 파일의 수집 시각만 바꾸지 않는다.
- 새 run이 생겨도 이전 run의 로컬 파일·S3 원본·병합본·검수 CSV·API 데이터가 포함된 분석 산출물·버전 보관본의 기한은 따로 관리한다.
- S3 버전 관리가 켜져 있으면 삭제 마커만 만드는 것으로 삭제를 완료하지 않는다. 비현재 버전과 복사본도 처리한다.
- 11/20~22 수집분의 12/20~22 갱신·삭제 담당자는 수집 실행 기록에 지정한다. 아직 팀원이 실행·검토하지 않은 작업을 완료로 기록하지 않는다.

## 검수

Parquet 컬럼·타입·행 수, manifest 버전·샤드·시각, 가명화 키 버전 일치, 중복 ID와 부모 댓글 연결을 확인한다. S3 공유 전 로컬 검사를 통과해야 한다.

## 근거

- 제품명세서 9장 데이터: 비공개 S3 하나, runs/ 원본·분석 산출물 저장
- [01. 수집 범위 확정안](https://app.notion.com/p/01-41bb674b662180b18b51f5dbe3a51cd5): 5·8·9장
- 팀 공통 규격 v1: Parquet(zstd), UTC, null, 샤드별 파일 4종

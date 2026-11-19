# 채널·댓글 ID 가명 처리 결정

담당: 박지연 · VIDI-44 · 기준일: 2026-11-19

## 시점과 범위

수집 단계에서, **최초 디스크 기록 전**에 처리한다. 원본 응답 JSON·작성자 ID 목록·매핑표를 파일이나 로그로 남기지 않는다.

1. 동결된 마스터 영상 ID로 공개 댓글·답글을 받는다.
2. 응답의 원본 authorChannelId와 영상 업로더 channelId로 필요한 채널 정보를 조회한다. channels.list는 팀 규칙대로 50개씩 나눠 호출한다.
3. 원본 ID를 가명화하고 videos/channels/comments 행을 만든다.
4. 공통 스키마를 검증한 뒤 Parquet으로 쓴다. 필요한 원본 ID는 메모리에서만 사용한다.

- 작성자와 업로더 모두 같은 channel 도메인으로 변환한다. 같은 채널은 영상·샤드가 달라도 같은 값이다.
- 댓글 ID와 parent_comment_id는 같은 comment 도메인으로 변환한다. 영상 ID는 원문 문자열을 유지한다.
- author_name, 작성자 표시명, 핸들, 채널 URL, 프로필 이미지 URL은 저장하지 않는다.
- 작성자 ID가 없으면 author_channel_id는 null이다. 이름으로 대체하거나 가짜 계정 ID를 만들지 않는다.
- 채널 조회가 실패하면 같은 가명 channel_id에 nullable 메타데이터를 null로 두고 실패 건수를 기록한다. 생성일을 추정하지 않는다.
- 중단 후 필요한 작성자 정보는 해당 영상의 댓글을 다시 조회해 얻는다. 원본 ID의 영구 캐시나 재식별 매핑을 만들지 않는다.

## 방식

단순 해시나 공개 솔트 대신 **공통 비밀키를 사용하는 HMAC-SHA256**을 쓴다. 별도 솔트는 사용하지 않고 비밀키를 안전하게 관리한다.

```text
channel_id        = "ch_" + HMAC-SHA256(key, UTF8("channel:" + raw_channel_id)).hexdigest()
comment_id        = "cm_" + HMAC-SHA256(key, UTF8("comment:" + raw_comment_id)).hexdigest()
parent_comment_id = 위 comment_id와 같은 공식, 부모가 없으면 null
```

- 키 문자열도 UTF-8로 인코딩한다. 32바이트 이상의 무작위 키를 사용한다.
- 결과는 접두사 + 소문자 hex 64자리이다. 자르거나 대소문자를 바꾸지 않는다.
- 원본 ID는 대소문자·기호를 그대로 유지한다.
- 이미 가명화된 ID는 다른 키로 다시 해시하지 않는다. 새 수집의 원본 변환 함수에 넣으면 오류로 처리한다.
- 구현은 `src/collection/pseudonyms.py`의 Pseudonymizer를 사용한다. 실제 수집에는 합성 전용 키 폴백을 두지 않는다.

## 키 보관·공유·버전

| 항목 | 결정 |
| --- | --- |
| 공통 키 | `DATA_PSEUDONYM_KEY` 환경변수 |
| 키 버전 | `DATA_PSEUDONYM_KEY_VERSION`, 기본 `v1` |
| 보관 위치 | SSM Parameter Store SecureString의 `/vidi/data/pseudonym-key/v1` |
| 읽기 권한 | 수집·병합 담당 팀원과 해당 실행 역할에만 최소 권한 부여 |
| 전달 | 팀원이 권한 있는 AWS 세션에서 받아 실행 환경에만 주입 |
| 기록 가능 값 | manifest의 pseudonym_key_version만 기록 |

- 키를 Notion·채팅·Git·CSV·Parquet·manifest·명령 로그에 공유하지 않는다. 셸 tracing을 끈다.
- 같은 실행·세 샤드는 같은 키와 버전을 사용한다. 환경변수 유무·버전만 확인하고 값을 출력하지 않는다.
- 등록·권한 부여는 별도 보안 설정 작업이다. 이 PR에서 실제 키 생성·SSM 등록을 수행했다고 표시하지 않는다.
- 키를 교체하면 버전과 실행을 함께 바꾼다. 다른 버전의 샤드는 병합하지 않으며 기존 가명 ID를 역변환하지 않는다.

## 화면·CSV와 정책 범위

내부 HMAC ID는 계정 신호 계산과 테이블 조인에만 쓴다. 제품명세서 COM-04·05에 따라 화면·외부 CSV에는 내부 ID를 내보내지 않고 후보 안의 익명 표시값을 쓴다. 내부 검수 기록에도 실제 계정명·채널 주소를 옮기지 않는다.

가명화는 익명화나 YouTube 정책 예외를 뜻하지 않는다. 댓글 본문·영상 ID가 남으며, 계정 활동 연결 등은 팀이 확인한 YouTube API 정책과 승인 범위 안에서 수행한다. 키가 있다고 실제 데이터 사용·공개가 허가됐다고 판단하지 않는다. API 데이터와 관련 복사본의 30일 갱신·삭제 기준은 그대로 적용한다.

## 확인

```bash
python -m unittest discover -s tests -p 'test_pseudonyms.py' -v
```

팀원 두 명의 검토와 공통 키 배포는 PR 체크리스트에서 별도로 확인한다.

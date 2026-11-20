# vidi-data
vidi 데이터 레포지토리입니다.

## 공통 수집 스키마

[규격 v1과 API 응답 변환·Parquet 저장 방법](docs/data/schema-v1.md)을 따른다 (VIDI-45).
ID 생성은 VIDI-44의 가명 처리 모듈, 저장·S3 경로는 VIDI-43의 규칙을 따른다.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_collection_schema.py' -v
```


## 제품 사전 (`resources/product_dict.json`)

댓글에서 제품을 찾을 때 쓰는 참조 파일입니다. 필드 정의, 별칭 규칙, 구분 기준의 상세는 제품 사전 규칙 문서(Notion "데이터 수집 준비 > 02. 제품 사전 규칙과 인지도 구분")에 있습니다.

```python
import json
with open("resources/product_dict.json", encoding="utf-8") as f:
    products = json.load(f)
```

### 인지도 구분 (`awareness_tier`)

| 값 | 뜻 |
| --- | --- |
| `유명` | 수상 이력이 있거나 리뷰 수가 많아 평소 언급량이 많은 제품 |
| `중간` | 유명과 저언급 사이 |
| `저언급` | 수상 이력이 없고 리뷰 수가 적어 평소 언급량이 적은 제품 |

- 구분에 쓰는 수치 기준은 임시값이라 위 Notion 문서에서만 관리합니다.
- 저언급은 평소 언급량이 적다는 통계적 성질만 나타냅니다. 특정 브랜드·제품에 대한 의혹이 아닙니다.

### 판매 상태 (`status`)와 매칭

| 값 | 뜻 | 매칭 |
| --- | --- | --- |
| `active` | 판매 중 | 함 |
| `unknown` | 판매 여부를 확인하지 못함. 시도한 내용은 `status_note`에 있음 | 함 |
| `discontinued` | 단종. 댓글에 계속 나오므로 남김 | 함 |
| `retired` | 사전 범위에서 뺀 제품. ID 재사용을 막으려고 남김 | 안 함 |

## 댓글 수집기 실행

[실행·호출 예산·중단 후 재개 안내](docs/data/collector.md)를 따른다.
동결된 마스터 목록을 입력하며 검색은 다시 수행하지 않는다.
VIDI-44의 가명 처리 모듈이 먼저 필요하며 실제 키는 환경변수로만 전달한다.

```bash
python -m src.collection --help
python -m unittest discover -s tests -v
```

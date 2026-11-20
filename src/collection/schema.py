"""규격 v1: 저장 직전 검증하는 Arrow 스키마와 테이블 관계."""

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime

import pyarrow as pa

SCHEMA_VERSION = "v1"
UTC_TIME = pa.timestamp("us", tz="UTC")


def _schema(fields):
    return pa.schema(
        [pa.field(name, kind, nullable=nullable) for name, kind, nullable in fields]
    )


SCHEMAS = {
    "videos": _schema(
        [
            ("video_id", pa.string(), False),
            ("channel_id", pa.string(), False),
            ("title", pa.string(), False),
            ("description", pa.string(), True),
            ("published_at", UTC_TIME, False),
            ("duration_seconds", pa.int64(), True),
            ("view_count", pa.int64(), True),
            ("like_count", pa.int64(), True),
            ("comment_count", pa.int64(), True),
            ("collected_at", UTC_TIME, False),
        ]
    ),
    "channels": _schema(
        [
            ("channel_id", pa.string(), False),
            ("published_at", UTC_TIME, True),
            ("subscriber_count", pa.int64(), True),
            ("video_count", pa.int64(), True),
            ("view_count", pa.int64(), True),
            ("collected_at", UTC_TIME, False),
        ]
    ),
    "comments": _schema(
        [
            ("comment_id", pa.string(), False),
            ("video_id", pa.string(), False),
            ("author_channel_id", pa.string(), True),
            ("parent_comment_id", pa.string(), True),
            ("text", pa.string(), False),
            ("published_at", UTC_TIME, False),
            ("updated_at", UTC_TIME, True),
            ("like_count", pa.int64(), True),
            ("reply_count", pa.int64(), True),
            ("collected_at", UTC_TIME, False),
        ]
    ),
}
PRIMARY_KEYS = {
    "videos": "video_id",
    "channels": "channel_id",
    "comments": "comment_id",
}
PSEUDONYM_COLUMNS = {
    "channel_id": r"ch_[0-9a-f]{64}",
    "author_channel_id": r"ch_[0-9a-f]{64}",
    "comment_id": r"cm_[0-9a-f]{64}",
    "parent_comment_id": r"cm_[0-9a-f]{64}",
}


def utc(value: str | datetime) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("시간에는 UTC 또는 명시적인 시간대가 필요합니다")
    return value.astimezone(UTC)


def table_from_rows(name: str, rows: Iterable[Mapping]) -> pa.Table:
    schema = SCHEMAS[name]
    prepared = []
    for row in rows:
        if set(row) != set(schema.names):
            raise ValueError(f"{name}: 컬럼 이름이 규격 v1과 다릅니다")
        converted = dict(row)
        for field in schema:
            value = converted[field.name]
            if value is None:
                if not field.nullable:
                    raise ValueError(f"{name}.{field.name}: 필수 값이 없습니다")
                continue
            if pa.types.is_timestamp(field.type):
                converted[field.name] = utc(value)
            elif pa.types.is_integer(field.type):
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"{name}.{field.name}: 0 이상의 정수가 필요합니다")
            elif not isinstance(value, str):
                raise ValueError(f"{name}.{field.name}: 문자열이 필요합니다")
        prepared.append(converted)
    table = pa.Table.from_pylist(prepared, schema=schema)
    _validate_table(name, table)
    return table


def _validate_table(name: str, table: pa.Table) -> None:
    if not table.schema.equals(SCHEMAS[name], check_metadata=False):
        raise ValueError(f"{name}: 컬럼 순서·타입·null 허용 여부가 규격과 다릅니다")
    seen = set()
    for row in table.to_pylist():
        for field in table.schema:
            value = row[field.name]
            if value is None:
                if not field.nullable:
                    raise ValueError(f"{name}.{field.name}: 필수 값이 없습니다")
                continue
            if field.name in PSEUDONYM_COLUMNS and not re.fullmatch(
                PSEUDONYM_COLUMNS[field.name], value
            ):
                raise ValueError(f"{name}.{field.name}: 가명 ID 형식이 아닙니다")
            if pa.types.is_integer(field.type) and value < 0:
                raise ValueError(f"{name}.{field.name}: 음수 개수는 허용하지 않습니다")
        key = row[PRIMARY_KEYS[name]]
        if not key or key in seen:
            raise ValueError(f"{name}: 기본키가 비었거나 중복되었습니다")
        seen.add(key)
        published, collected = row["published_at"], row["collected_at"]
        if published is not None and published > collected:
            raise ValueError(f"{name}: 게시 시각이 수집 시각보다 늦습니다")
        if name == "comments":
            updated = row["updated_at"]
            if updated is not None and not published <= updated <= collected:
                raise ValueError("comments: 수정 시각이 게시·수집 시각 범위 밖입니다")
            if row["parent_comment_id"] is not None and row["reply_count"] is not None:
                raise ValueError("comments: 답글 행의 reply_count는 null이어야 합니다")


def validate_tables(tables: Mapping[str, pa.Table], *, partial: bool = False) -> None:
    if set(tables) != set(SCHEMAS):
        raise ValueError("videos/channels/comments 테이블 3종이 필요합니다")
    for name, table in tables.items():
        _validate_table(name, table)
    videos = {r["video_id"]: r for r in tables["videos"].to_pylist()}
    channels = set(tables["channels"]["channel_id"].to_pylist())
    comments = {r["comment_id"]: r for r in tables["comments"].to_pylist()}
    if any(v["channel_id"] not in channels for v in videos.values()):
        raise ValueError("videos: 업로더의 channels 행이 없습니다")
    for row in comments.values():
        if row["video_id"] not in videos:
            raise ValueError("comments: 소속 videos 행이 없습니다")
        author = row["author_channel_id"]
        if author is not None and author not in channels:
            raise ValueError("comments: 작성자의 channels 행이 없습니다")
        if row["published_at"] < videos[row["video_id"]]["published_at"]:
            raise ValueError("comments: 댓글이 영상 게시 시각보다 이릅니다")
        parent_id = row["parent_comment_id"]
        if parent_id is None:
            continue
        parent = comments.get(parent_id)
        if parent is None:
            if partial:
                continue
            raise ValueError("comments: 부모 댓글이 없습니다")
        if (
            parent["parent_comment_id"] is not None
            or parent["video_id"] != row["video_id"]
        ):
            raise ValueError("comments: 최상위 부모 댓글·영상 연결이 잘못되었습니다")
        if row["published_at"] < parent["published_at"]:
            raise ValueError("comments: 답글이 부모 댓글보다 이릅니다")

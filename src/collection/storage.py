"""검증한 규격 v1 샤드를 새 디렉터리에 원자적으로 저장한다."""

import hashlib
import json
import re
import tempfile
from datetime import timedelta
from pathlib import Path

import pyarrow.parquet as pq

from .schema import SCHEMA_VERSION, utc, validate_tables


def _iso(value):
    return utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def write_collection(
    tables,
    destination,
    *,
    run_id,
    shard_id,
    pseudonym_key_version,
    started_at,
    finished_at,
    collection_status="complete",
    source_kind="youtube",
    failed_video_ids=(),
):
    if not re.fullmatch(r"run_\d{8}T\d{6}Z", run_id):
        raise ValueError("run_id 형식이 올바르지 않습니다")
    utc(run_id[4:12] + "T" + run_id[13:19] + "Z")
    if shard_id not in {"shard_01", "shard_02", "shard_03", "merged"}:
        raise ValueError("shard_id 형식이 올바르지 않습니다")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", pseudonym_key_version):
        raise ValueError("키 버전 형식이 올바르지 않습니다")
    if collection_status not in {"complete", "partial"} or source_kind not in {
        "youtube",
        "synthetic",
    }:
        raise ValueError("수집 상태 또는 출처가 올바르지 않습니다")
    failures = list(failed_video_ids)
    if any(not isinstance(v, str) or not v for v in failures):
        raise ValueError("실패 영상 ID는 문자열이어야 합니다")
    if collection_status == "complete" and failures:
        raise ValueError("실패 영상이 있는 실행을 complete로 기록하지 않습니다")
    validate_tables(tables, partial=collection_status == "partial")
    samples = [
        v for table in tables.values() for v in table["collected_at"].to_pylist()
    ]
    start, finish = utc(started_at), utc(finished_at)
    oldest, newest = (min(samples), max(samples)) if samples else (start, finish)
    if not start <= oldest <= newest <= finish:
        raise ValueError("실행 시작·종료가 행의 수집 시각을 포함하지 않습니다")
    target = Path(destination)
    if target.exists():
        raise FileExistsError("완료된 수집 디렉터리를 덮어쓰지 않습니다")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".partial-", dir=target.parent
    ) as temporary:
        stage = Path(temporary)
        files = {}
        for name, table in tables.items():
            file = stage / f"{name}.parquet"
            pq.write_table(table, file, compression="zstd")
            files[file.name] = {"sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
        comment_ids = set(tables["comments"]["comment_id"].to_pylist())
        missing_parents = sum(
            value is not None and value not in comment_ids
            for value in tables["comments"]["parent_comment_id"].to_pylist()
        )
        manifest = {
            "kind": "collection",
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "shard_id": shard_id,
            "source_kind": source_kind,
            "pseudonym_key_version": pseudonym_key_version,
            "started_at": _iso(start),
            "finished_at": _iso(finish),
            "collected_at_min": _iso(oldest) if samples else None,
            "collected_at_max": _iso(newest) if samples else None,
            "expires_at": _iso(oldest + timedelta(days=30)),
            "collection_status": collection_status,
            "failed_video_ids": failures,
            "row_counts": {name: table.num_rows for name, table in tables.items()},
            "quality": {"missing_parent_count": missing_parents},
            "files": files,
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        stage.rename(target)
    return target

"""동결된 영상 목록의 댓글·답글을 수집하고 완료 영상 단위로 재개한다."""

import hashlib
import json
import re
from pathlib import Path

import pyarrow.parquet as pq

from .api_rows import YouTubeRows
from .schema import SCHEMAS, table_from_rows, utc, validate_tables
from .storage import write_collection
from .youtube import APIError, QuotaStop, atomic_json


class CollectionChanged(APIError):
    pass


def duration_seconds(value):
    if value is None:
        return None
    match = re.fullmatch(
        r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?", value
    )
    if not match or not any(match.groups()):
        raise APIError("invalidDuration")
    return int(
        sum(
            float(v or 0) * factor
            for v, factor in zip(match.groups(), [86400, 3600, 60, 1])
        )
    )


def load_master(path, shard):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("동결된 마스터 목록은 비어 있지 않은 JSON 배열이어야 합니다")
    seen, selected = set(), []
    for row in data:
        video = row["video_id"]
        owner = row["shard"]
        owner = (
            f"shard_{owner:02d}"
            if isinstance(owner, int) and not isinstance(owner, bool)
            else owner
        )
        if (
            not isinstance(video, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video)
            or video in seen
        ):
            raise ValueError("영상 ID 형식 또는 마스터 중복을 확인해야 합니다")
        if owner not in {"shard_01", "shard_02", "shard_03"}:
            raise ValueError("마스터의 샤드 배정이 올바르지 않습니다")
        seen.add(video)
        if owner == shard:
            count = row.get("comment_count", row.get("commentCount", 0))
            if count is None:
                count = 0
            selected.append((video, int(count) >= 5000))
    return [v for v, heavy in sorted(selected, key=lambda item: item[1])]


def collect_video(client, ids, video_id):
    data = client.get("videos", part="snippet,statistics,contentDetails", id=video_id)
    if len(data.get("items", [])) != 1:
        raise APIError("videoNotFound")
    resource = data["items"][0]
    mapper = YouTubeRows(ids, client.clock())
    video = mapper.video(
        resource,
        duration_seconds=duration_seconds(
            resource.get("contentDetails", {}).get("duration")
        ),
    )
    comments, channels = {}, {}

    def capture(resources, total=None):
        converter = YouTubeRows(ids, client.clock())
        raw_authors = {
            (r["snippet"].get("authorChannelId") or {}).get("value") for r in resources
        }
        raw_authors.discard(None)
        pending = [v for v in raw_authors if ids.channel_id(v) not in channels]
        for start in range(0, len(pending), 50):
            chunk = pending[start : start + 50]
            response = client.get(
                "channels", part="snippet,statistics", id=",".join(chunk)
            )
            channel_mapper = YouTubeRows(ids, client.clock())
            found = {r["id"]: r for r in response.get("items", [])}
            for raw in chunk:
                row = channel_mapper.channel(raw, found.get(raw))
                channels[row["channel_id"]] = row
        for item in resources:
            row = converter.comment(
                item,
                total_reply_count=total.get(item["id"])
                if isinstance(total, dict)
                else total,
            )
            comments[row["comment_id"]] = row

    # 업로더도 channels 표에 넣는다. 원본 ID는 이 함수 메모리 밖으로 저장하지 않는다.
    uploader = resource["snippet"]["channelId"]
    response = client.get("channels", part="snippet,statistics", id=uploader)
    found = {r["id"]: r for r in response.get("items", [])}
    row = YouTubeRows(ids, client.clock()).channel(uploader, found.get(uploader))
    channels[row["channel_id"]] = row

    for page in client.pages(
        "commentThreads",
        part="snippet,replies",
        videoId=video_id,
        order="time",
        maxResults=100,
        textFormat="plainText",
    ):
        threads = page.get("items", [])
        tops = [thread["snippet"]["topLevelComment"] for thread in threads]
        totals = {
            thread["snippet"]["topLevelComment"]["id"]: thread["snippet"][
                "totalReplyCount"
            ]
            for thread in threads
        }
        capture(tops, totals)
        embedded_complete, remaining = [], []
        for thread in threads:
            top = thread["snippet"]["topLevelComment"]
            embedded = thread.get("replies", {}).get("comments", [])
            if totals[top["id"]] > len({r["id"] for r in embedded}):
                remaining.append(top["id"])
            else:
                embedded_complete.extend(embedded)
        capture(embedded_complete)
        for parent in remaining:
            for replies in client.pages(
                "comments",
                part="snippet",
                parentId=parent,
                maxResults=100,
                textFormat="plainText",
            ):
                capture(replies.get("items", []))
        reply_counts = {}
        for row in comments.values():
            parent = row["parent_comment_id"]
            if parent is not None:
                reply_counts[parent] = reply_counts.get(parent, 0) + 1
        if any(
            reply_counts.get(ids.comment_id(parent), 0) < total
            for parent, total in totals.items()
        ):
            raise APIError("incompleteReplies")
    tables = {
        name: table_from_rows(name, rows)
        for name, rows in {
            "videos": [video],
            "channels": list(channels.values()),
            "comments": list(comments.values()),
        }.items()
    }
    validate_tables(tables)
    return tables


def _collect_shard(client, ids, *, master, shard, run_id, work, output):
    work, output = Path(work), Path(output)
    if output.exists():
        raise FileExistsError(
            "출력 경로가 이미 있습니다; 재개 시 새 출력 경로를 지정해야 합니다"
        )
    if not re.fullmatch(r"run_\d{8}T\d{6}Z", run_id):
        raise ValueError("run_id 형식이 올바르지 않습니다")
    utc(run_id[4:12] + "T" + run_id[13:19] + "Z")
    if shard not in {"shard_01", "shard_02", "shard_03"}:
        raise ValueError("샤드가 올바르지 않습니다")
    videos = load_master(master, shard)
    binding = {
        "master_sha256": hashlib.sha256(Path(master).read_bytes()).hexdigest(),
        "shard": shard,
        "run_id": run_id,
        "key_version": ids.key_version,
        "key_check": ids.channel_id("vidi-collector-key-confirmation"),
    }
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = work / "state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state["binding"] != binding:
            raise CollectionChanged("masterOrKeyChanged")
    else:
        state = {
            "binding": binding,
            "started_at": client.clock().isoformat(),
            "errors": {},
        }
        atomic_json(state_path, state)
    selected, unfinished = [], []
    for index, video in enumerate(videos):
        checkpoint = work / "videos" / video
        if (checkpoint / "manifest.json").exists():
            meta = json.loads((checkpoint / "manifest.json").read_text())
            if utc(meta["expires_at"]) <= client.clock():
                raise CollectionChanged("checkpointExpired")
            if (
                meta["run_id"] != run_id
                or meta["pseudonym_key_version"] != ids.key_version
            ):
                raise CollectionChanged("checkpointMetadataMismatch")
            for file, info in meta["files"].items():
                if (
                    hashlib.sha256((checkpoint / file).read_bytes()).hexdigest()
                    != info["sha256"]
                ):
                    raise CollectionChanged("checkpointChecksumMismatch")
            tables = {
                name: pq.read_table(checkpoint / f"{name}.parquet") for name in SCHEMAS
            }
            validate_tables(tables)
            selected.append(tables)
            continue
        started = client.clock()
        try:
            tables = collect_video(client, ids, video)
            write_collection(
                tables,
                checkpoint,
                run_id=run_id,
                shard_id=shard,
                pseudonym_key_version=ids.key_version,
                started_at=started,
                finished_at=client.clock(),
            )
            selected.append(tables)
            state["errors"].pop(video, None)
        except QuotaStop as error:
            state["errors"][video] = error.reason
            unfinished.extend(videos[index:])
            atomic_json(state_path, state)
            break
        except APIError as error:
            state["errors"][video] = error.reason
            unfinished.append(video)
        atomic_json(state_path, state)
    # 채널은 여러 영상에서 반복되므로 최신 수집 행 전체를 남긴다.
    combined = {}
    for name in SCHEMAS:
        key = {
            "videos": "video_id",
            "channels": "channel_id",
            "comments": "comment_id",
        }[name]
        unique = {}
        for group in selected:
            for row in group[name].to_pylist():
                if (
                    row[key] not in unique
                    or row["collected_at"] > unique[row[key]]["collected_at"]
                ):
                    unique[row[key]] = row
        combined[name] = table_from_rows(name, unique.values())
    write_collection(
        combined,
        output,
        run_id=run_id,
        shard_id=shard,
        pseudonym_key_version=ids.key_version,
        started_at=state["started_at"],
        finished_at=client.clock(),
        collection_status="partial" if unfinished else "complete",
        failed_video_ids=unfinished,
    )
    return not unfinished


def collect_shard(client, ids, **options):
    import fcntl

    work = Path(options["work"])
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (work / ".collector.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CollectionChanged("collectorAlreadyRunning") from None
        return _collect_shard(client, ids, **options)

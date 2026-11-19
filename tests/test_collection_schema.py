import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.collection.api_rows import YouTubeRows
from src.collection.schema import SCHEMAS, table_from_rows, utc, validate_tables
from src.collection.storage import write_collection

CH = "ch_" + "a" * 64
AUTHOR = "ch_" + "b" * 64
CM = "cm_" + "a" * 64
REPLY = "cm_" + "b" * 64
COLLECTED = "2026-11-18T03:20:00Z"


class FixtureEncoder:
    """고정 가명 ID로 API 변환을 검증한다. HMAC 자체는 VIDI-44에서 검증한다."""

    def channel_id(self, value):
        return {None: None, "uploader": CH, "author": AUTHOR}[value]

    def comment_id(self, value):
        return {None: None, "top": CM, "reply": REPLY}[value]


def rows():
    mapper = YouTubeRows(FixtureEncoder(), COLLECTED)
    video = mapper.video(
        {
            "id": "video000001",
            "snippet": {
                "channelId": "uploader",
                "title": "테스트",
                "publishedAt": "2026-11-18T03:00:00Z",
            },
            "statistics": {"viewCount": "0", "commentCount": "100"},
        },
        duration_seconds=180,
    )
    channels = [mapper.channel(raw, None) for raw in ["uploader", "author"]]
    channels[0]["collected_at"] = utc("2026-11-18T03:10:00Z")

    def comment(identifier, parent=None):
        snippet = {
            "videoId": video["video_id"],
            "authorChannelId": {"value": "author"},
            "textDisplay": '쉼표, 줄바꿈\n"따옴표" 😊',
            "textOriginal": "사용하지 않는 값",
            "publishedAt": "2026-11-18T03:05:00Z",
            "likeCount": 0,
        }
        if parent:
            snippet["parentId"] = parent
            snippet["publishedAt"] = "2026-11-18T03:06:00Z"
        return mapper.comment(
            {"id": identifier, "snippet": snippet}, total_reply_count=10
        )

    return {
        "videos": [video],
        "channels": channels,
        "comments": [comment("top"), comment("reply", "top")],
    }


def tables(value=None):
    return {
        name: table_from_rows(name, items)
        for name, items in (rows() if value is None else value).items()
    }


def save(value, destination, **options):
    return write_collection(
        value,
        destination,
        run_id="run_20261118T030000Z",
        shard_id="shard_01",
        pseudonym_key_version="v1",
        started_at="2026-11-18T03:00:00Z",
        finished_at="2026-11-18T03:30:00Z",
        **options,
    )


class CollectionSchemaTests(unittest.TestCase):
    def test_api_text_display_no_author_name_and_parent_link(self):
        value = rows()
        top, reply = value["comments"]
        self.assertEqual(top["text"], '쉼표, 줄바꿈\n"따옴표" 😊')
        self.assertNotIn("author_name", top)
        self.assertIsNone(top["parent_comment_id"])
        self.assertEqual(reply["parent_comment_id"], top["comment_id"])
        self.assertEqual(
            top["reply_count"], 10
        )  # API 전체 수를 부분 수집 행 수 1로 바꾸지 않는다.
        self.assertIsNone(reply["reply_count"])
        validate_tables(tables(value))

    def test_channel_metadata_missing_or_private_stays_null(self):
        mapper = YouTubeRows(FixtureEncoder(), COLLECTED)
        missing = mapper.channel("author", None)
        self.assertIsNone(missing["published_at"])
        self.assertIsNone(missing["subscriber_count"])
        hidden = mapper.channel(
            "author",
            {
                "id": "author",
                "statistics": {"hiddenSubscriberCount": True, "subscriberCount": "999"},
            },
        )
        self.assertIsNone(hidden["subscriber_count"])
        public = mapper.channel(
            "author",
            {
                "id": "author",
                "statistics": {"hiddenSubscriberCount": False, "subscriberCount": "0"},
            },
        )
        self.assertEqual(public["subscriber_count"], 0)
        with self.assertRaises(ValueError):
            mapper.channel("author", {"id": "uploader"})

    def test_utc_conversion_and_naive_rejection(self):
        self.assertEqual(utc("2026-11-18T12:20:00+09:00"), utc(COLLECTED))
        with self.assertRaises(ValueError):
            utc(datetime(2026, 11, 18))

    def test_extra_columns_and_raw_ids_cannot_be_persisted(self):
        for change in [
            lambda r: r.update(author_name="저장 금지"),
            lambda r: r.update(author_channel_id="raw-channel"),
            lambda r: r.update(comment_id="raw-comment"),
        ]:
            value = rows()
            change(value["comments"][0])
            with self.assertRaises(ValueError):
                tables(value)

    def test_duplicate_null_and_negative_values_rejected(self):
        for change in [
            lambda r: r["comments"].append(copy.deepcopy(r["comments"][0])),
            lambda r: r["comments"][0].update(text=None),
            lambda r: r["comments"][0].update(like_count=-1),
            lambda r: r["videos"][0].update(comment_count=True),
        ]:
            value = rows()
            change(value)
            with self.assertRaises(ValueError):
                tables(value)

    def test_future_and_backdated_edits_rejected(self):
        for change in [
            {"published_at": "2026-11-18T03:21:00Z"},
            {"updated_at": "2026-11-18T03:21:00Z"},
            {"updated_at": "2026-11-18T03:01:00Z"},
        ]:
            value = rows()
            value["comments"][0].update(change)
            with self.assertRaises(ValueError):
                tables(value)

    def test_missing_foreign_rows_rejected(self):
        for name, index in [("channels", 0), ("channels", 1), ("videos", 0)]:
            value = rows()
            value[name].pop(index)
            with self.assertRaises(ValueError):
                validate_tables(tables(value))

    def test_parent_must_be_top_level_and_same_video(self):
        value = rows()
        value["comments"][0]["parent_comment_id"] = REPLY
        value["comments"][0]["reply_count"] = None
        with self.assertRaises(ValueError):
            validate_tables(tables(value))

    def test_missing_parent_requires_partial_status(self):
        value = rows()
        value["comments"].pop(0)
        with self.assertRaises(ValueError):
            validate_tables(tables(value))
        with tempfile.TemporaryDirectory() as root:
            out = save(
                tables(value), Path(root) / "partial", collection_status="partial"
            )
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(manifest["quality"]["missing_parent_count"], 1)

    def test_parquet_round_trip_manifest_and_no_overwrite(self):
        value = tables()
        with tempfile.TemporaryDirectory() as root:
            out = save(value, Path(root) / "shard_01")
            self.assertEqual(
                {p.name for p in out.iterdir()},
                {
                    "videos.parquet",
                    "channels.parquet",
                    "comments.parquet",
                    "manifest.json",
                },
            )
            for name, table in value.items():
                file = out / f"{name}.parquet"
                loaded = pq.read_table(file)
                self.assertTrue(table.equals(loaded))
                self.assertEqual(
                    loaded.schema.field("published_at").type,
                    pa.timestamp("us", tz="UTC"),
                )
                self.assertEqual(
                    pq.ParquetFile(file).metadata.row_group(0).column(0).compression,
                    "ZSTD",
                )
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(manifest["expires_at"], "2026-12-18T03:10:00.000000Z")
            self.assertEqual(manifest["row_counts"]["comments"], 2)
            self.assertEqual(
                manifest["files"]["comments.parquet"]["sha256"],
                hashlib.sha256((out / "comments.parquet").read_bytes()).hexdigest(),
            )
            with self.assertRaises(FileExistsError):
                save(value, out)
            self.assertFalse(
                any(p.name.startswith(".partial-") for p in Path(root).iterdir())
            )

    def test_empty_shard_keeps_schema_and_records_zero(self):
        empty = tables({name: [] for name in SCHEMAS})
        validate_tables(empty)
        with tempfile.TemporaryDirectory() as root:
            out = save(empty, Path(root) / "empty")
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertIsNone(manifest["collected_at_min"])
            self.assertEqual(manifest["row_counts"], {name: 0 for name in SCHEMAS})
            self.assertEqual(
                pq.read_table(out / "comments.parquet").schema, SCHEMAS["comments"]
            )

    def test_failed_run_cannot_be_complete(self):
        with tempfile.TemporaryDirectory() as root, self.assertRaises(ValueError):
            save(tables(), Path(root) / "bad", failed_video_ids=["failed-video"])

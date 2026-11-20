import hashlib
import io
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError

import pyarrow.parquet as pq

from src.collection.collector import (
    CollectionChanged,
    collect_shard,
    collect_video,
    load_master,
)
from src.collection.youtube import APIError, Budget, QuotaStop, YouTubeClient

NOW = datetime(2026, 11, 20, 3, tzinfo=UTC)
VIDEO = "abcdefghijk"
OTHER = "12345678901"


class IDs:
    key_version = "v1"

    def channel_id(self, value):
        return (
            None
            if value is None
            else "ch_" + hashlib.sha256(value.encode()).hexdigest()
        )

    def comment_id(self, value):
        return (
            None
            if value is None
            else "cm_" + hashlib.sha256(value.encode()).hexdigest()
        )


def comment(identifier, video=VIDEO, parent=None):
    snippet = {
        "videoId": video,
        "authorChannelId": {"value": "raw-author"},
        "authorDisplayName": "DO NOT STORE",
        "publishedAt": "2026-11-19T01:00:00Z",
        "textDisplay": "후기, 줄바꿈\n😊",
        "likeCount": 0,
    }
    if parent:
        snippet["parentId"] = parent
    return {"id": identifier, "snippet": snippet}


class Fake:
    def __init__(self):
        self.calls = []
        self.fail = set()

    def clock(self):
        return NOW

    def get(self, endpoint, **params):
        self.calls.append((endpoint, params))
        if endpoint == "videos":
            if params["id"] in self.fail:
                raise QuotaStop("quotaExceeded")
            return {
                "items": [
                    {
                        "id": params["id"],
                        "snippet": {
                            "channelId": "raw-uploader",
                            "title": "테스트",
                            "publishedAt": "2026-11-19T00:00:00Z",
                        },
                        "contentDetails": {"duration": "PT3M"},
                        "statistics": {"commentCount": "10"},
                    }
                ]
            }
        if endpoint == "channels":
            return {"items": []}  # 삭제·비공개 채널도 가명 참조 행 유지
        raise AssertionError(endpoint)

    def pages(self, endpoint, **params):
        self.calls.append((endpoint, params))
        if endpoint == "commentThreads":
            v = params["videoId"]
            yield {
                "items": [
                    {
                        "id": "NOT-THE-COMMENT-ID",
                        "snippet": {
                            "topLevelComment": comment("top-" + v, v),
                            "totalReplyCount": 2,
                        },
                        "replies": {
                            "comments": [comment("reply1-" + v, v, "top-" + v)]
                        },
                    }
                ]
            }
            yield {
                "items": [
                    {
                        "snippet": {
                            "topLevelComment": comment("second-" + v, v),
                            "totalReplyCount": 0,
                        }
                    }
                ]
            }
        else:
            parent = params["parentId"]
            v = parent[4:]
            yield {"items": [comment("reply1-" + v, v, parent)]}
            yield {"items": [comment("reply2-" + v, v, parent)]}


class CollectorTests(unittest.TestCase):
    def test_threads_replies_and_private_channels(self):
        api = Fake()
        tables = collect_video(api, IDs(), VIDEO)
        self.assertEqual(tables["comments"].num_rows, 4)
        self.assertEqual(
            set(tables["comments"]["comment_id"].to_pylist()),
            {
                IDs().comment_id(i + VIDEO)
                for i in ["top-", "second-", "reply1-", "reply2-"]
            },
        )
        self.assertEqual(tables["channels"].num_rows, 2)
        self.assertEqual(tables["channels"]["published_at"].null_count, 2)
        self.assertIn(
            (
                "comments",
                {
                    "part": "snippet",
                    "parentId": "top-" + VIDEO,
                    "maxResults": 100,
                    "textFormat": "plainText",
                },
            ),
            api.calls,
        )
        self.assertTrue(
            all(len(p["id"].split(",")) <= 50 for e, p in api.calls if e == "channels")
        )

    def test_resume_skips_complete_video_and_rejects_new_master_or_key(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            master = root / "master.json"
            master.write_text(
                json.dumps(
                    [{"video_id": VIDEO, "shard": 1}, {"video_id": OTHER, "shard": 1}]
                )
            )
            options = {
                "master": master,
                "shard": "shard_01",
                "run_id": "run_20261120T030000Z",
                "work": root / "work",
            }
            api = Fake()
            api.fail.add(OTHER)
            self.assertFalse(
                collect_shard(api, IDs(), output=root / "partial", **options)
            )
            self.assertEqual(
                json.loads((root / "partial/manifest.json").read_text())[
                    "failed_video_ids"
                ],
                [OTHER],
            )
            raw = b"".join(
                f.read_bytes() for f in (root / "work").rglob("*") if f.is_file()
            )
            self.assertNotIn(b"raw-author", raw)
            self.assertNotIn(b"raw-uploader", raw)
            self.assertNotIn(b"DO NOT STORE", raw)
            api = Fake()
            self.assertTrue(
                collect_shard(api, IDs(), output=root / "complete", **options)
            )
            self.assertNotIn(
                ("videos", {"part": "snippet,statistics,contentDetails", "id": VIDEO}),
                api.calls,
            )
            self.assertEqual(
                pq.read_table(root / "complete/comments.parquet").num_rows, 8
            )
            other_ids = IDs()
            other_ids.key_version = "v2"
            with self.assertRaises(CollectionChanged):
                collect_shard(Fake(), other_ids, output=root / "new-key", **options)
            master.write_text(json.dumps([{"video_id": OTHER, "shard": 1}]))
            with self.assertRaises(CollectionChanged):
                collect_shard(Fake(), IDs(), output=root / "new-master", **options)

    def test_disabled_video_is_partial_and_no_raw_error_saved(self):
        class Disabled(Fake):
            def pages(self, *args, **kwargs):
                raise APIError("commentsDisabled")

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            master = root / "master.json"
            master.write_text(json.dumps([{"video_id": VIDEO, "shard": 1}]))
            result = collect_shard(
                Disabled(),
                IDs(),
                master=master,
                shard="shard_01",
                run_id="run_20261120T030000Z",
                work=root / "work",
                output=root / "out",
            )
            self.assertFalse(result)
            meta = json.loads((root / "out/manifest.json").read_text())
            self.assertEqual(meta["row_counts"]["comments"], 0)

    def test_master_dedup_and_large_video_deferral(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "master.json"
            path.write_text(
                json.dumps(
                    [
                        {"video_id": VIDEO, "shard": 1, "commentCount": 5000},
                        {"video_id": OTHER, "shard": "shard_01", "comment_count": 50},
                    ]
                )
            )
            self.assertEqual(load_master(path, "shard_01"), [OTHER, VIDEO])
            path.write_text(json.dumps([{"video_id": VIDEO, "shard": 1}] * 2))
            with self.assertRaises(ValueError):
                load_master(path, "shard_01")

    def test_budget_counts_retries_and_resets_at_pacific_midnight(self):
        with tempfile.TemporaryDirectory() as root:
            day = [datetime(2026, 11, 20, 7, 59, tzinfo=UTC)]
            budget = Budget(Path(root) / "budget.json", 2, clock=lambda: day[0])
            budget.take()
            budget.take()
            with self.assertRaises(QuotaStop):
                budget.take()
            day[0] += timedelta(minutes=2)
            budget.take()
            self.assertEqual(json.loads(budget.path.read_text())["used"], 1)

    def test_http_retry_budget_and_key_not_in_url(self):
        calls = []

        def opener(req, **kwargs):
            calls.append(req)
            if len(calls) == 1:
                raise HTTPError(req.full_url, 503, "unavailable", {}, io.BytesIO(b"{}"))
            return io.BytesIO(b'{"items":[]}')

        with tempfile.TemporaryDirectory() as root:
            api = YouTubeClient(
                "secret-value",
                Budget(Path(root) / "budget.json"),
                opener=opener,
                sleep=lambda _: None,
            )
            self.assertEqual(api.get("videos", id=VIDEO), {"items": []})
            self.assertEqual(len(calls), 2)
            self.assertNotIn("secret-value", calls[0].full_url)
            self.assertEqual(json.loads(api.budget.path.read_text())["used"], 2)

    def test_pagination_uses_tokens_even_when_items_empty(self):
        with tempfile.TemporaryDirectory() as root:
            api = YouTubeClient("key", Budget(Path(root) / "b"))
            results = iter([{"items": [], "nextPageToken": "next"}, {"items": [1]}])
            api.get = lambda *a, **k: next(results)
            self.assertEqual(len(list(api.pages("comments"))), 2)
            api.get = lambda *a, **k: {"nextPageToken": "same"}
            with self.assertRaises(APIError):
                list(api.pages("comments"))


class BatchingTests(unittest.TestCase):
    def test_authors_are_batched_by_fifty(self):
        class Many(Fake):
            def pages(self, endpoint, **params):
                items = []
                for i in range(101):
                    resource = comment("top" + str(i))
                    resource["snippet"]["authorChannelId"]["value"] = "author-" + str(i)
                    items.append(
                        {"snippet": {"topLevelComment": resource, "totalReplyCount": 0}}
                    )
                yield {"items": items}

        api = Many()
        result = collect_video(api, IDs(), VIDEO)
        sizes = [len(p["id"].split(",")) for e, p in api.calls if e == "channels"]
        self.assertEqual(sizes, [1, 50, 50, 1])
        self.assertEqual(result["comments"].num_rows, 101)

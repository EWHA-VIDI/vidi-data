import io
import json
import urllib.error
import urllib.parse

from src.master_list.baseline import fetch_baseline
from src.master_list.merge import build_master
from src.master_list.rules import RULES, categories_of, check, info_of, parse_duration, shard2_rule
from src.master_list.search import PLANS, hits_from_log, max_calls, run_search
from src.master_list.youtube import QuotaExceeded, YouTube

BASE = "UCqrNqg3UgVoD3Sa-F_TxuSA"  # 기준선 채널 (디렉터파이)
OTHER = "UCotherotherotherother1"
OTHER2 = "UCotherotherotherother2"


def video(vid, *, title="수분크림 비교 리뷰", desc="", published="2026-03-01T03:00:00Z", comments="120",
          duration="PT10M", paid=False, channel=BASE):
    return {
        "id": vid,
        "snippet": {"channelId": channel, "channelTitle": "ch", "title": title, "description": desc,
                    "publishedAt": published, "liveBroadcastContent": "none"},
        "statistics": {} if comments is None else {"commentCount": comments},
        "contentDetails": {"duration": duration},
        "paidProductPlacementDetails": {"hasPaidProductPlacement": paid},
    }


def hit(vid, kind, rank=1, at="2026-11-20T01:00:00Z", kw="kw"):
    return dict(video_id=vid, hit=kind, keyword=kw, period=0, rank=rank, searched_at=at)


# ---------------------------------------------------------------- rules


def test_길이와_카테고리():
    assert parse_duration("PT1H2M3S") == 3723 and parse_duration(None) is None
    assert categories_of("겨울 스킨케어 루틴") == []
    assert categories_of("스킨 토너 추천") == ["toner"]


def test_기준선_조건():
    r = RULES["baseline"]
    assert check(video("a"), r) == []
    assert check(video("b", published="2025-10-31T14:59:59Z"), r) == ["기간 밖"]  # KST 10/31 23:59
    assert check(video("c", published="2025-10-31T15:00:00Z"), r) == []  # KST 11/1 00:00
    assert check(video("d", published="2026-11-11T15:00:00Z"), r) == ["기간 밖"]  # KST 11/12
    assert check(video("e", comments=None), r) == ["댓글 비활성화"]
    assert check(video("f", comments="49"), r) == ["댓글 50개 미만"]
    assert "하위 카테고리 없음" in check(video("g", title="레이저 시술 후기"), r)


def test_고민_조건():
    milia = video("m", title="눈밑 비립종 없애는 법", comments="12", published="2021-06-01T00:00:00Z")
    assert shard2_rule(milia).name == "milia" and check(milia, RULES["milia"]) == []
    comedone = video("c", title="좁쌀여드름 홈케어", comments="12")
    assert shard2_rule(comedone).name == "comedone"
    assert check(comedone, RULES["comedone"]) == ["댓글 50개 미만"]
    assert check(video("a", title="트러블 진정 앰플"), RULES["acne"]) == []
    assert check(video("a2", title="수분크림 리뷰"), RULES["acne"]) == ["필수 단어 없음(여드름/트러블)"]
    assert check(video("a3", title="여드름 앰플", published="2023-12-31T14:00:00Z"), RULES["acne"]) == ["기간 밖"]


def test_기록_값():
    i = info_of(video("a", duration="PT2M30S", paid=True, comments="6000", title="여드름 압출 크림 하울"))
    assert i.short_like is True and i.duration_sec == 150 and i.has_paid_placement is True
    assert i.defer is True and "haul" in i.video_types and "압출" in i.exclude_hints


# ---------------------------------------------------------------- merge


def test_샤드_순서와_플래그():
    videos = {
        # 기준선 채널 + 여드름 검색: 샤드 3 조건 통과 → 샤드 3, hit_acne 표시
        "b1": video("b1", title="여드름 크림 비교"),
        # 기준선 채널이지만 기간 밖 + 비립종 검색 → 샤드 2
        "b2": video("b2", title="비립종 크림", published="2025-04-03T00:00:00Z", comments="20"),
        # 비립종·여드름 둘 다 걸림 → 샤드 2
        "x1": video("x1", title="비립종 여드름 홈케어", channel=OTHER, comments="30"),
        # 여드름만 → 샤드 1
        "x2": video("x2", title="여드름 앰플", channel=OTHER2),
        # 여드름 검색에 걸렸지만 댓글 부족 → 탈락
        "x3": video("x3", title="여드름 앰플", channel=OTHER2, comments="10"),
    }
    hits = [hit("b1", "acne"), hit("b2", "milia"), hit("x1", "milia"), hit("x1", "acne", rank=3),
            hit("x2", "acne"), hit("x3", "acne")]
    res = build_master(videos, hits, "now")
    shard = {r["video_id"]: r["shard"] for r in res["master"]}
    assert shard == {"b1": 3, "b2": 2, "x1": 2, "x2": 1}
    b1 = next(r for r in res["master"] if r["video_id"] == "b1")
    assert b1["hit_acne"] and b1["baseline_channel"] and b1["search_rank"] == 1
    x1 = next(r for r in res["master"] if r["video_id"] == "x1")
    assert x1["hit_milia"] and x1["hit_acne"] and x1["rule"] == "milia"
    assert [d["video_id"] for d in res["dropped"]] == ["x3"]
    assert "댓글 50개 미만" in res["dropped"][0]["drop_reasons"]


def test_고민_샤드_채널_상한에_샤드3_고민영상을_센다():
    # 기준선 채널에서 샤드 3이 여드름 영상 2개를 가져가면, 같은 채널의 샤드 1 자리는 3개
    videos = {f"b{i}": video(f"b{i}", title="여드름 크림 리뷰", comments=str(500 - i)) for i in range(2)}
    videos.update({
        f"o{i}": video(f"o{i}", title="여드름 홈케어", comments=str(300 - i), published="2024-06-01T00:00:00Z")
        for i in range(5)
    })  # 기준선 기간 밖 → 샤드 1 후보
    hits = [hit(v, "acne") for v in videos]
    res = build_master(videos, hits, "now")
    s1 = [r for r in res["master"] if r["shard"] == 1]
    assert len(s1) == 3 and {r["video_id"] for r in s1} == {"o0", "o1", "o2"}
    assert all("상한" in d["drop_reasons"] for d in res["dropped"])


def test_기준선_상한_초과는_고민_샤드로_넘어간다():
    videos = {f"v{i:02d}": video(f"v{i:02d}", title="여드름 크림 리뷰", comments=str(1000 - i)) for i in range(27)}
    hits = [hit("v26", "acne")]
    res = build_master(videos, hits, "now")
    shard = {r["video_id"]: r["shard"] for r in res["master"]}
    assert sum(1 for s in shard.values() if s == 3) == 25
    assert shard["v26"] == 1  # 상한에 걸린 뒤 여드름 조건으로 샤드 1
    assert [d["video_id"] for d in res["dropped"]] == ["v25"]


# ---------------------------------------------------------------- API (가짜)


class FakeAPI:
    def __init__(self, videos, search_pages=None, quota_after=None):
        self.videos = {v["id"]: v for v in videos}
        self.search_pages = search_pages or {}
        self.quota_after = quota_after
        self.calls = []

    def __call__(self, url, timeout=None):
        path, query = url.split("?", 1)
        q = dict(urllib.parse.parse_qsl(query))
        resource = path.rsplit("/", 1)[1]
        self.calls.append((resource, q))
        if resource == "search":
            n = sum(1 for c in self.calls if c[0] == "search")
            if self.quota_after is not None and n > self.quota_after:
                raise urllib.error.HTTPError(url, 403, "x", {}, io.BytesIO(b'{"reason":"quotaExceeded"}'))
            page = int(q.get("pageToken", "1"))
            ids = self.search_pages.get((q["q"], page), [])
            body = {"items": [{"id": {"videoId": v}} for v in ids]}
            if (q["q"], page + 1) in self.search_pages:
                body["nextPageToken"] = str(page + 1)
        elif resource == "channels":
            body = {"items": [{"id": c, "contentDetails": {"relatedPlaylists": {"uploads": "UU" + c[2:]}}}
                              for c in q["id"].split(",")]}
        elif resource == "playlistItems":
            if q["playlistId"] != "UU" + BASE[2:]:
                body = {"items": []}
            elif "pageToken" not in q:
                body = {"items": [{"contentDetails": {"videoId": v, "videoPublishedAt": "2026-03-01T03:00:00Z"}}
                                  for v in ("new1", "new2")], "nextPageToken": "p2"}
            else:
                body = {"items": [{"contentDetails": {"videoId": "old", "videoPublishedAt": "2025-01-01T00:00:00Z"}}],
                        "nextPageToken": "p3"}
        elif resource == "videos":
            body = {"items": [self.videos[i] for i in q["id"].split(",") if i in self.videos]}
        return io.BytesIO(json.dumps(body).encode())


def test_기준선_업로드_목록은_기간_전_페이지에서_멈춘다():
    fake = FakeAPI([video("new1"), video("new2")])
    res = fetch_baseline(YouTube("k", opener=fake), channels={BASE: "디렉터파이"})
    assert [v["id"] for v in res["videos"]] == ["new1", "new2"]
    assert sum(1 for c in fake.calls if c[0] == "playlistItems") == 2


def test_검색은_할당량에서_멈추고_이어서_한다(tmp_path):
    kw = PLANS["comedone"].keywords[0]
    pages = {(kw, 1): ["a", "b"], (kw, 2): ["c"]}
    log = tmp_path / "log.jsonl"
    # 첫 실행: search 1회만 허용 → 멈춤
    yt = YouTube("k", opener=FakeAPI([], pages, quota_after=1))
    r1 = run_search(yt, ["comedone"], log)
    assert not r1["finished"] and r1["new_pages"] == 1
    # 다시 실행: 2페이지부터 이어서
    fake = FakeAPI([], pages)
    r2 = run_search(YouTube("k", opener=fake), ["comedone"], log)
    assert r2["finished"]
    first = [c for c in fake.calls if c[0] == "search" and c[1]["q"] == kw]
    assert first[0][1]["pageToken"] == "2"
    ranks = {(h["video_id"], h["rank"]) for h in hits_from_log(log) if h["keyword"] == kw}
    assert ranks >= {("a", 1), ("b", 2), ("c", 51)}
    params = first[0][1]
    assert params["regionCode"] == "KR" and params["relevanceLanguage"] == "ko" and params["type"] == "video"


def test_최대_검색_횟수는_문서와_같다():
    assert max_calls(["acne"]) == 88
    assert max_calls(["milia", "comedone"]) == 72


def test_할당량_에러():
    import pytest
    yt = YouTube("k", opener=FakeAPI([], {}, quota_after=0))
    with pytest.raises(QuotaExceeded):
        yt.search_page("x", "a", "b", None)

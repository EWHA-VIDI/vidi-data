"""세 샤드 후보를 합쳐 마스터 영상 목록을 만든다 (01 문서 5장).

한 영상이 여러 경로에 걸리면 아래 순서로 조건을 확인해 처음 통과한 샤드가 수집한다.
    1. 기준선 채널 영상 → 샤드 3 조건(채널당 25개 포함)을 통과하면 샤드 3
    2. 비립종·좁쌀 검색 → 샤드 2 조건을 통과하면 샤드 2
    3. 여드름 검색 → 샤드 1 조건을 통과하면 샤드 1
고민 샤드의 채널당 상한(5개)에는 같은 채널에서 샤드 3이 수집한 그 고민 영상도 센다.
상한에 걸린 영상은 다음 순서의 샤드로 넘어간다.
"""

from __future__ import annotations

from collections import defaultdict

from .baseline import BASELINE_CHANNELS
from .rules import RULES, check, info_of, shard2_rule

MIN_CHANNELS = {"acne": 15, "milia": 8, "comedone": 8, "baseline": 0}  # 기준선은 16곳 목록 고정이라 최소 기준 없음


def build_master(videos: dict[str, dict], hits: list[dict], listed_at: str,
                 baseline_channels: dict[str, str] = BASELINE_CHANNELS) -> dict:
    by_video: dict[str, list[dict]] = defaultdict(list)
    for h in hits:
        by_video[h["video_id"]].append(h)

    rows: dict[str, dict] = {}
    for vid, video in videos.items():
        info = info_of(video)
        vh = by_video.get(vid, [])
        kinds = {h["hit"] for h in vh}
        first = min(vh, key=lambda h: (h["searched_at"], h["rank"])) if vh else None
        is_base = info.channel_id in baseline_channels
        rule2 = shard2_rule(video)
        reasons = {
            3: check(video, RULES["baseline"]) if is_base else ["기준선 채널 아님"],
            2: check(video, rule2) if kinds & {"milia", "comedone"} else ["비립종·좁쌀 검색에 안 걸림"],
            1: check(video, RULES["acne"]) if "acne" in kinds else ["여드름 검색에 안 걸림"],
        }
        rows[vid] = dict(
            info=info, kinds=kinds, first=first, is_base=is_base, rule2=rule2.name,
            reasons=reasons, shard=None, rule=None, note=[],
        )

    def count(r):
        return r["info"].comment_count or 0

    def assign(shard: int, rule_of, cap_of, cap_used):
        pool = [r for r in rows.values() if r["shard"] is None and not r["reasons"][shard]]
        by_ch = defaultdict(list)
        for r in pool:
            by_ch[r["info"].channel_id].append(r)
        for ch, items in by_ch.items():
            items.sort(key=lambda r: (-count(r), r["info"].video_id))
            slots = max(0, cap_of(items[0]) - cap_used(ch, items[0]))
            for i, r in enumerate(items):
                if i < slots:
                    r["shard"], r["rule"] = shard, rule_of(r)
                else:
                    r["reasons"][shard] = [f"채널당 상한 초과({rule_of(r)})"]

    shard3 = lambda: [r for r in rows.values() if r["shard"] == 3]  # noqa: E731

    # 1) 샤드 3
    assign(3, lambda r: "baseline", lambda r: RULES["baseline"].channel_cap, lambda ch, r: 0)

    # 2) 샤드 2: 같은 채널에서 샤드 3이 수집한 비립종·좁쌀 영상도 상한에 셈
    def used2(ch, r):
        return sum(1 for s in shard3() if s["info"].channel_id == ch and s["kinds"] & {"milia", "comedone"})

    assign(2, lambda r: r["rule2"], lambda r: RULES[r["rule2"]].channel_cap, used2)

    # 3) 샤드 1: 같은 채널에서 샤드 3이 수집한 여드름 영상도 상한에 셈
    def used1(ch, r):
        return sum(1 for s in shard3() if s["info"].channel_id == ch and "acne" in s["kinds"])

    assign(1, lambda r: "acne", lambda r: RULES["acne"].channel_cap, used1)

    master, dropped = [], []
    for r in rows.values():
        i, f = r["info"], r["first"]
        row = {
            "video_id": i.video_id,
            "channel_id": i.channel_id,
            "shard": r["shard"],
            "hit_acne": "acne" in r["kinds"],
            "hit_milia": "milia" in r["kinds"],
            "hit_comedone": "comedone" in r["kinds"],
            "baseline_channel": r["is_base"],
            "search_rank": f["rank"] if f else None,
            "search_keyword": f["keyword"] if f else None,
            "searched_at": f["searched_at"] if f else None,
            "categories": "|".join(i.categories),
            "has_paid_placement": i.has_paid_placement,
            "duration_sec": i.duration_sec,
            "short_like": i.short_like,
            "commentCount": i.comment_count,
            # 확인용 열
            "rule": r["rule"],
            "channel_title": baseline_channels.get(i.channel_id, i.channel_title),
            "title": i.title,
            "published_at": i.published_at,
            "video_types": "|".join(i.video_types),
            "type_check": r["shard"] == 3 and not i.video_types,  # 기준선 영상 종류 사람이 확인
            "exclude_hints": "|".join(i.exclude_hints) if r["shard"] in (1, 2) else "",  # 고민 샤드 제외 후보
            "defer": i.defer,
            "listed_at": listed_at,
        }
        if r["shard"]:
            master.append(row)
        elif r["kinds"] or r["is_base"]:
            row["drop_reasons"] = " / ".join(
                f"샤드{s}: " + "|".join(r["reasons"][s]) for s in (3, 2, 1)
                if not r["reasons"][s][0].endswith(("아님", "안 걸림"))
            ) or "어느 경로에도 해당 없음"
            dropped.append(row)

    master.sort(key=lambda x: (x["shard"], x["channel_id"], -(x["commentCount"] or 0)))
    return dict(master=master, dropped=dropped, summary=summarize(master))


def summarize(master: list[dict]) -> list[dict]:
    out = []
    for rule in ("acne", "milia", "comedone", "baseline"):
        rs = [r for r in master if r["rule"] == rule]
        channels = len({r["channel_id"] for r in rs})
        out.append(dict(
            rule=rule,
            shard={"acne": 1, "milia": 2, "comedone": 2, "baseline": 3}[rule],
            videos=len(rs),
            channels=channels,
            min_channels=MIN_CHANNELS[rule],
            channels_ok=channels >= MIN_CHANNELS[rule],
            comments=sum(r["commentCount"] or 0 for r in rs),
            short_like=sum(bool(r["short_like"]) for r in rs),
            check_needed=sum(bool(r["type_check"] or r["exclude_hints"]) for r in rs),
            defer=sum(bool(r["defer"]) for r in rs),
        ))
    return out

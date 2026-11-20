"""샤드 1·2 키워드 검색 (01 문서 7.1, 7.2장).

검색 결과는 시점마다 바뀌므로 결과 영상 ID·순위·검색 시각을 그대로 남긴다.
search.list는 프로젝트당 하루 100회라, 할당량이 끝나면 지금까지 받은 페이지를 저장하고 멈춘다.
같은 명령을 다시 실행하면 끝난 (키워드, 기간)은 건너뛰고 이어서 한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .youtube import QuotaExceeded, YouTube


@dataclass(frozen=True)
class SearchPlan:
    hit: str  # acne / milia / comedone (마스터 목록 hit_* 열)
    shard: int
    keywords: tuple[str, ...]
    periods: tuple[tuple[str, str], ...]  # (publishedAfter, publishedBefore) RFC 3339
    max_pages: int


_ACNE_PERIODS = (("2024-01-01T00:00:00+09:00", "2025-01-01T00:00:00+09:00"),
                 ("2025-01-01T00:00:00+09:00", "2026-11-12T00:00:00+09:00"))
_MILIA_PERIODS = (("2021-01-01T00:00:00+09:00", "2024-01-01T00:00:00+09:00"),
                  ("2024-01-01T00:00:00+09:00", "2026-11-12T00:00:00+09:00"))

PLANS = {
    "acne": SearchPlan("acne", 1, (
        "여드름 화장품 추천", "여드름 앰플 추천", "트러블 세럼 추천", "여드름 피부 선크림",
        "여드름 앰플 리뷰", "트러블 진정 앰플 비교",
        "여드름 없애는 법", "여드름 홈케어",
        "여드름 흉터 화장품", "여드름 자국 없애는 법", "여드름 자국 크림",
    ), _ACNE_PERIODS, 4),
    "milia": SearchPlan("milia", 2, (
        "비립종 화장품", "비립종 아이크림", "비립종 크림",
        "비립종 제거", "비립종 없애는 법", "비립종 홈케어",
        "비립종 원인", "눈밑 비립종",
    ), _MILIA_PERIODS, 3),
    "comedone": SearchPlan("comedone", 2, ("좁쌀여드름", "좁쌀 화장품", "좁쌀여드름 없애는 법"), _ACNE_PERIODS, 4),
}
SHARD_PLANS = {1: ["acne"], 2: ["milia", "comedone"]}


def max_calls(names: list[str]) -> int:
    return sum(len(PLANS[n].keywords) * len(PLANS[n].periods) * PLANS[n].max_pages for n in names)


def _load(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_search(yt: YouTube, names: list[str], log: Path) -> dict:
    """검색 페이지마다 한 줄씩 log(jsonl)에 덧붙인다. 반환: 이번 실행 통계와 완료 여부."""
    pages = _load(log)
    state: dict[tuple, dict] = {}
    for p in pages:  # (plan, keyword, period) → 마지막 페이지
        state[(p["plan"], p["keyword"], p["period"])] = p
    done_before = sum(1 for p in state.values() if p["done"])

    log.parent.mkdir(parents=True, exist_ok=True)
    new_pages, finished = 0, True
    try:
        with log.open("a", encoding="utf-8") as f:
            for name in names:
                plan = PLANS[name]
                for kw in plan.keywords:
                    for pi, (after, before) in enumerate(plan.periods):
                        key = (name, kw, pi)
                        last = state.get(key)
                        if last and last["done"]:
                            continue
                        page = last["page"] + 1 if last else 1
                        token = last["next_page_token"] if last else None
                        while True:
                            res = yt.search_page(kw, after, before, token)
                            ids = [it["id"]["videoId"] for it in res.get("items", []) if it.get("id", {}).get("videoId")]
                            token = res.get("nextPageToken")
                            done = not token or page >= plan.max_pages
                            row = dict(
                                plan=name, shard=plan.shard, keyword=kw, period=pi, page=page,
                                video_ids=ids, next_page_token=token, done=done,
                                searched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            )
                            f.write(json.dumps(row, ensure_ascii=False) + "\n")
                            f.flush()
                            state[key] = row
                            new_pages += 1
                            if done:
                                break
                            page += 1
    except QuotaExceeded:
        finished = False
    total = sum(len(PLANS[n].keywords) * len(PLANS[n].periods) for n in names)
    complete = sum(1 for k, v in state.items() if k[0] in names and v["done"])
    return dict(new_pages=new_pages, finished=finished, complete=complete, total=total, done_before=done_before)


def hits_from_log(log: Path) -> list[dict]:
    """검색 로그 → 영상별 걸린 기록 (순위는 (키워드, 기간) 안에서 1부터)."""
    hits = []
    for p in _load(log):
        for i, vid in enumerate(p["video_ids"]):
            hits.append(dict(
                video_id=vid, hit=p["plan"], keyword=p["keyword"], period=p["period"],
                rank=(p["page"] - 1) * 50 + i + 1, searched_at=p["searched_at"],
            ))
    return hits

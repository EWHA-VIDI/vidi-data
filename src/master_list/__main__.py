"""마스터 영상 목록 만들기 (01 문서 4·5·7장).

    export YOUTUBE_API_KEY=...
    python -m src.master_list baseline      # 샤드 3: 기준선 채널 16곳 영상 (약 130단위)
    python -m src.master_list search 1      # 샤드 1: 여드름 검색 (search 최대 88회)
    python -m src.master_list search 2      # 샤드 2: 비립종·좁쌀 검색 (search 최대 72회)
    python -m src.master_list merge         # 세 샤드 합치기 → 마스터 목록 (API 호출 거의 없음)
    python -m src.master_list status        # 진행 상황과 남은 search 호출 수

search.list는 프로젝트당 하루 100회. 중간에 할당량이 끝나면 저장하고 멈추며,
한국 시간 오후 5시(할당량 초기화) 이후 같은 명령을 다시 실행하면 이어서 한다.
모든 결과는 --out 폴더(기본 data/master_list, gitignore 대상)에 남는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .baseline import fetch_baseline
from .merge import build_master
from .search import PLANS, SHARD_PLANS, hits_from_log, max_calls, run_search
from .youtube import QuotaExceeded, YouTube

CACHE = "videos_raw.jsonl"  # videos.list 원본 (영상 ID별 최신 1개)
SEARCH_LOG = "search_log.jsonl"


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="", encoding="utf-8-sig") as f:  # 엑셀에서 한글이 안 깨지게 BOM
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows({k: "" if v is None else v for k, v in r.items()} for r in rows)


def load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            v = json.loads(line)
            out[v["id"]] = v
    return out


def save_cache(path: Path, videos: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for v in videos:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")


def api() -> YouTube:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        sys.exit("환경 변수 YOUTUBE_API_KEY가 없습니다.")
    return YouTube(key)


def cmd_baseline(out: Path, args) -> None:
    yt = api()
    try:
        res = fetch_baseline(yt, max_pages=args.max_pages)
    except QuotaExceeded as e:
        sys.exit(f"{e} (쓴 단위: {yt.units})")
    save_cache(out / CACHE, res["videos"])
    (out / "baseline_ids.json").write_text(json.dumps([v["id"] for v in res["videos"]]), encoding="utf-8")
    print(f"기준선 영상 {len(res['videos'])}개 저장, API {yt.units}단위")
    if res["missing_channels"]:
        print("업로드 목록을 못 찾은 채널:", ", ".join(res["missing_channels"]))


def cmd_search(out: Path, args) -> None:
    names = SHARD_PLANS[args.shard]
    yt = api()
    log = out / SEARCH_LOG
    res = run_search(yt, names, log)
    print(f"search {yt.search_calls}회 사용, (키워드×기간) {res['complete']}/{res['total']} 완료")
    # 새로 걸린 영상 메타데이터 받기
    cache = load_cache(out / CACHE)
    want = [h["video_id"] for h in hits_from_log(log) if h["hit"] in names and h["video_id"] not in cache]
    try:
        new = yt.videos(want)
    except QuotaExceeded as e:
        sys.exit(f"{e}")
    save_cache(out / CACHE, new)
    print(f"영상 정보 {len(new)}개 추가, API {yt.units}단위")
    if not res["finished"]:
        print("search 할당량을 다 써서 멈췄습니다. 한국 시간 오후 5시 이후 같은 명령을 다시 실행하세요.")


def cmd_merge(out: Path, args) -> None:
    cache = load_cache(out / CACHE)
    if not cache:
        sys.exit("영상 정보가 없습니다. baseline / search를 먼저 실행하세요.")
    hits = hits_from_log(out / SEARCH_LOG)
    listed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = build_master(cache, hits, listed_at)
    write_csv(out / "master_videos.csv", res["master"])
    write_csv(out / "master_dropped.csv", res["dropped"])
    write_csv(out / "master_summary.csv", res["summary"])
    print(f"{'조건':<10}{'샤드':>4}{'영상':>6}{'채널':>6}{'최소':>6}{'댓글':>10}{'쇼츠':>6}{'확인':>6}")
    for s in res["summary"]:
        mark = "" if s["channels_ok"] else "  ← 채널 수 부족"
        print(f"{s['rule']:<10}{s['shard']:>4}{s['videos']:>6}{s['channels']:>6}{s['min_channels']:>6}"
              f"{s['comments']:>10,}{s['short_like']:>6}{s['check_needed']:>6}{mark}")
    print(f"마스터 {len(res['master'])}개, 탈락 {len(res['dropped'])}개 -> {out / 'master_videos.csv'}")


def cmd_status(out: Path, args) -> None:
    log = out / SEARCH_LOG
    pages = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()] if log.exists() else []
    for shard, names in SHARD_PLANS.items():
        done = {(p["plan"], p["keyword"], p["period"]) for p in pages if p["done"] and p["plan"] in names}
        total = sum(len(PLANS[n].keywords) * len(PLANS[n].periods) for n in names)
        used = sum(1 for p in pages if p["plan"] in names)
        print(f"샤드 {shard}: (키워드×기간) {len(done)}/{total} 완료, search {used}회 사용 (최대 {max_calls(names)}회)")
    print(f"영상 정보 캐시 {len(load_cache(out / CACHE))}개")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m src.master_list")
    ap.add_argument("--out", default="data/master_list")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("baseline")
    b.add_argument("--max-pages", type=int, default=10)
    s = sub.add_parser("search")
    s.add_argument("shard", type=int, choices=[1, 2])
    sub.add_parser("merge")
    sub.add_parser("status")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    {"baseline": cmd_baseline, "search": cmd_search, "merge": cmd_merge, "status": cmd_status}[args.cmd](out, args)


if __name__ == "__main__":
    main()

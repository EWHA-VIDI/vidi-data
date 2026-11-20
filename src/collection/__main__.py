"""python -m src.collection: 동결된 마스터 목록의 담당 샤드만 수집한다."""

import argparse
import os
import sys

from .collector import collect_shard
from .youtube import APIError, Budget, YouTubeClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", required=True)
    parser.add_argument(
        "--shard", required=True, choices=["shard_01", "shard_02", "shard_03"]
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument(
        "--budget-file",
        required=True,
        help="같은 Google 프로젝트에서 공유하는 예산 파일",
    )
    parser.add_argument("--daily-budget", type=int, default=8000)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        from .pseudonyms import Pseudonymizer
    except ImportError:
        parser.exit(1, "VIDI-44 가명 처리 모듈을 먼저 머지해야 합니다.\n")
    try:
        client = YouTubeClient(
            os.environ.get("YOUTUBE_API_KEY"),
            Budget(args.budget_file, args.daily_budget),
        )
        complete = collect_shard(
            client,
            Pseudonymizer.from_environment(),
            master=args.master,
            shard=args.shard,
            run_id=args.run_id,
            work=args.work,
            output=args.out,
        )
    except (APIError, ValueError, FileExistsError, KeyError):
        parser.exit(
            1,
            "수집 설정·입력 또는 체크포인트 검증 실패; 원본 응답·키는 출력하지 않습니다.\n",
        )
    print(
        "수집 완료"
        if complete
        else "부분 수집 저장 완료; 할당량·실패 내역 확인 후 같은 work와 새 out으로 재개"
    )
    return 0 if complete else 2


if __name__ == "__main__":
    sys.exit(main())

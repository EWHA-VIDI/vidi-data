"""합성 데이터 만들기.

    python -m src.synthetic --out data/synthetic/run01
    python -m src.synthetic --out data/synthetic/run02 --seed 7 --campaigns 20 --videos 60
    python -m src.synthetic --inject-dir data/raw/run01 --out data/synthetic/inject01

계정 ID 가명화가 필요하면 환경 변수 VIDI_HASH_KEY를 설정한다 (키는 커밋하지 않는다).
"""

from __future__ import annotations

import argparse
import os

import pandas as pd

from .generator import Config, generate, inject, save
from .products import load_products


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m src.synthetic")
    ap.add_argument("--products", default="resources/product_dict.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--videos", type=int, default=40)
    ap.add_argument("--accounts", type=int, default=3000)
    ap.add_argument("--campaigns", type=int, default=12)
    ap.add_argument("--decoys", type=int, default=4)
    ap.add_argument("--inject-dir", help="videos.csv / channels.csv / comments.csv가 있는 폴더. 주면 군집만 심는다.")
    args = ap.parse_args(argv)

    cfg = Config(
        seed=args.seed,
        n_videos=args.videos,
        n_organic_accounts=args.accounts,
        n_campaigns=args.campaigns,
        n_decoys=args.decoys,
        hash_key=os.environ.get("VIDI_HASH_KEY") or None,
    )
    products = load_products(args.products)

    if args.inject_dir:
        read = lambda n: pd.read_csv(os.path.join(args.inject_dir, f"{n}.csv"))  # noqa: E731
        data = inject(read("videos"), read("channels"), read("comments"), products, cfg)
    else:
        data = generate(products, cfg)
    out = save(data, args.out, cfg)

    n_planted = sum(len(c["comment_ids"]) for c in data.clusters)
    print(
        f"videos={len(data.videos)} channels={len(data.channels)} comments={len(data.comments)} "
        f"planted={n_planted} clusters={len(data.clusters)} decoys={len(data.decoys)} -> {out}"
    )


if __name__ == "__main__":
    main()

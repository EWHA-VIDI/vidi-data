"""VIDI 합성 군집 생성기.

실제 수집 데이터와 같은 모양의 videos / channels / comments 표를 만들고,
그 안에 "조직적 바이럴" 군집을 정답(labels)과 함께 심는다.

- generate(): 배경(일반 댓글)부터 전부 합성으로 만든다. 승인 전에도 쓸 수 있다.
- inject():  이미 있는 표(실제 수집 데이터 등)에 군집만 심는다.

정답은 labels.json에만 남기고 CSV에는 넣지 않는다.
탐지 파이프라인은 CSV만 읽고, 평가할 때만 labels.json과 맞춰 본다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import text as T
from .products import Product

# 기본 컬럼 이름 (ERD 물리 이름 기준). 공통 스키마(VIDI-45)가 정해지면 Config.columns로 바꿔 저장한다.
VIDEO_COLS = ["video_id", "channel_id", "title", "uploaded_at"]
CHANNEL_COLS = ["channel_id", "channel_created_at", "subscriber_count"]
COMMENT_COLS = [
    "comment_id",
    "video_id",
    "author_channel_id",
    "parent_comment_id",
    "content",
    "like_count",
    "written_at",
]

# 난이도별 군집 특성
#   accounts: 계정 수, videos: 영상 수, hours: 업로드 후 몇 시간 안에 다는지,
#   fresh_ratio: 최근 생성 계정 비율, variation: 문장 변형 정도, reply_ratio: 서로 맞장구 답글 비율
DIFFICULTY = {
    "easy": dict(accounts=(8, 15), videos=(4, 8), hours=(0.2, 3), fresh_ratio=0.9, variation=0.2, reply_ratio=0.3),
    "medium": dict(accounts=(5, 10), videos=(3, 6), hours=(0.5, 12), fresh_ratio=0.6, variation=0.5, reply_ratio=0.15),
    "hard": dict(accounts=(3, 6), videos=(2, 4), hours=(1, 48), fresh_ratio=0.3, variation=0.8, reply_ratio=0.05),
}


@dataclass
class Config:
    seed: int = 42
    start: datetime = datetime(2026, 10, 1, tzinfo=timezone.utc)
    # 배경 데이터 (generate에서만 사용)
    n_videos: int = 40
    upload_span_days: int = 14
    n_organic_accounts: int = 3000
    organic_comments_per_video: tuple[int, int] = (80, 300)
    mention_ratio: float = 0.35  # 일반 댓글 중 제품을 언급하는 비율
    organic_reply_ratio: float = 0.15
    # 오탐 함정: 유명 제품을 자연스럽게 칭찬하는 댓글 묶음 (labels.decoys)
    n_decoys: int = 4
    # 심을 군집
    n_campaigns: int = 12
    difficulty_mix: dict[str, float] = field(default_factory=lambda: {"easy": 0.4, "medium": 0.4, "hard": 0.2})
    planted_tiers: tuple[str, ...] = ("중간", "저언급")
    # 저장 옵션
    hash_key: str | None = None  # 값이 있으면 계정 ID를 HMAC-SHA256(64자)으로 바꿔 저장
    columns: dict[str, dict[str, str]] = field(default_factory=dict)  # {"comments": {"content": "text"}, ...}


@dataclass
class SyntheticData:
    videos: pd.DataFrame
    channels: pd.DataFrame
    comments: pd.DataFrame
    clusters: list[dict]
    decoys: list[dict]


class _Builder:
    def __init__(self, cfg: Config, products: list[Product]):
        self.cfg = cfg
        self.products = products
        self.rng = random.Random(cfg.seed)
        self.nrng = np.random.default_rng(cfg.seed)
        self.channels: list[dict] = []
        self.comments: list[dict] = []
        self.clusters: list[dict] = []
        self.decoys: list[dict] = []
        self._ids: set[str] = set()

    # -- 기본 재료 ---------------------------------------------------------
    def new_id(self, prefix: str, n: int) -> str:
        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        while True:
            value = prefix + "".join(self.rng.choice(chars) for _ in range(n))
            if value not in self._ids:
                self._ids.add(value)
                return value

    def add_channel(self, created_at: datetime, subscribers: int) -> str:
        cid = self.new_id("UC", 22)
        self.channels.append(dict(channel_id=cid, channel_created_at=created_at, subscriber_count=subscribers))
        return cid

    def add_comment(self, video_id, author, text, written_at, likes, parent=None) -> str:
        cid = self.new_id("Ug", 24)
        if parent:
            cid = f"{parent}.{self.new_id('', 22)}"  # YouTube 답글 ID 형식: 부모ID.접미사
        self.comments.append(
            dict(
                comment_id=cid,
                video_id=video_id,
                author_channel_id=author,
                parent_comment_id=parent,
                content=text,
                like_count=int(likes),
                written_at=written_at,
            )
        )
        return cid

    def pick_product(self, pool: list[Product]) -> Product:
        weights = np.array([p.weight for p in pool])
        return pool[int(self.nrng.choice(len(pool), p=weights / weights.sum()))]

    # -- 배경 --------------------------------------------------------------
    def background(self) -> list[dict]:
        cfg, rng, nrng = self.cfg, self.rng, self.nrng
        videos = [
            dict(
                video_id=self.new_id("", 11),
                channel_id=self.new_id("UC", 22),
                title="[합성] 스킨케어 리뷰",
                uploaded_at=cfg.start + timedelta(hours=rng.uniform(0, 24 * cfg.upload_span_days)),
            )
            for _ in range(cfg.n_videos)
        ]
        accounts = [
            self.add_channel(cfg.start - timedelta(days=int(nrng.integers(200, 3000))), int(nrng.integers(0, 50)))
            for _ in range(cfg.n_organic_accounts)
        ]
        for v in videos:
            lo, hi = cfg.organic_comments_per_video
            for _ in range(int(nrng.integers(lo, hi))):
                product = self.pick_product(self.products) if rng.random() < cfg.mention_ratio else None
                at = v["uploaded_at"] + timedelta(hours=float(nrng.exponential(48)))
                cid = self.add_comment(v["video_id"], rng.choice(accounts), T.organic_text(rng, product), at, nrng.poisson(2))
                if rng.random() < cfg.organic_reply_ratio:
                    for _ in range(rng.randint(1, 2)):
                        self.add_comment(
                            v["video_id"], rng.choice(accounts), T.organic_reply(rng),
                            at + timedelta(hours=float(nrng.exponential(12))), nrng.poisson(0.5), parent=cid,
                        )
        self._decoys(videos, accounts)
        return videos

    def _decoys(self, videos: list[dict], accounts: list[str]) -> None:
        """유명 제품 칭찬 댓글 묶음. 문장은 비슷하지만 오래된 계정이 시간 차를 두고 단다."""
        famous = [p for p in self.products if p.tier == "유명"]
        if not famous:
            return
        for i in range(self.cfg.n_decoys):
            product = self.rng.choice(famous)
            ids = []
            for v in self.rng.sample(videos, k=min(len(videos), self.rng.randint(4, 10))):
                for _ in range(self.rng.randint(1, 3)):
                    at = v["uploaded_at"] + timedelta(hours=float(self.nrng.exponential(72)))
                    ids.append(self.add_comment(v["video_id"], self.rng.choice(accounts), T.praise_text(self.rng, product), at, self.nrng.poisson(3)))
            self.decoys.append(dict(decoy_id=f"D{i + 1:02d}", product_id=product.product_id, product=product.name, comment_ids=ids))

    # -- 군집 심기 ---------------------------------------------------------
    def plant(self, videos: list[dict]) -> None:
        cfg, rng, nrng = self.cfg, self.rng, self.nrng
        pool = [p for p in self.products if p.tier in cfg.planted_tiers] or self.products
        levels, probs = zip(*cfg.difficulty_mix.items())
        probs = np.array(probs, dtype=float) / sum(probs)
        offset = len(self.clusters)
        for i in range(cfg.n_campaigns):
            level = str(nrng.choice(levels, p=probs))
            d = DIFFICULTY[level]
            product = rng.choice(pool)
            targets = sorted(
                rng.sample(videos, k=min(len(videos), rng.randint(*d["videos"]))), key=lambda v: v["uploaded_at"]
            )
            accounts = []
            for _ in range(rng.randint(*d["accounts"])):
                fresh = rng.random() < d["fresh_ratio"]
                age = rng.randint(3, 60) if fresh else rng.randint(200, 2000)
                accounts.append(self.add_channel(targets[0]["uploaded_at"] - timedelta(days=age), int(nrng.integers(0, 5))))

            members: list[str] = []
            tops: list[tuple[str, dict, str, datetime]] = []
            for acc in accounts:
                # 계정마다 영상 1~3개에 댓글 → 같은 계정이 여러 영상에 반복 등장
                for v in rng.sample(targets, k=min(len(targets), rng.randint(1, 3))):
                    at = v["uploaded_at"] + timedelta(hours=rng.uniform(*d["hours"]))
                    cid = self.add_comment(v["video_id"], acc, T.campaign_text(rng, product, d["variation"]), at, nrng.poisson(5))
                    members.append(cid)
                    tops.append((cid, v, acc, at))
            # 같은 군집 계정끼리 맞장구 답글
            for cid, v, author, at in tops:
                others = [a for a in accounts if a != author]
                if others and rng.random() < d["reply_ratio"]:
                    members.append(
                        self.add_comment(
                            v["video_id"], rng.choice(others), T.support_reply(rng, product, d["variation"]),
                            at + timedelta(minutes=rng.uniform(5, 180)), nrng.poisson(2), parent=cid,
                        )
                    )
            self.clusters.append(
                dict(
                    synthetic_cluster_id=f"S{offset + i + 1:02d}",
                    product_id=product.product_id,
                    product=product.name,
                    awareness_tier=product.tier,
                    difficulty=level,
                    account_ids=accounts,
                    video_ids=[v["video_id"] for v in targets],
                    comment_ids=members,
                )
            )

    def result(self, videos: pd.DataFrame, channels: pd.DataFrame | None = None, comments: pd.DataFrame | None = None) -> SyntheticData:
        ch = pd.DataFrame(self.channels, columns=CHANNEL_COLS)
        cm = pd.DataFrame(self.comments, columns=COMMENT_COLS)
        if channels is not None:
            ch = pd.concat([channels, ch], ignore_index=True)
        if comments is not None:
            cm = pd.concat([comments, cm], ignore_index=True)
        cm = cm.sample(frac=1, random_state=self.cfg.seed).reset_index(drop=True)
        return SyntheticData(videos=videos, channels=ch, comments=cm, clusters=self.clusters, decoys=self.decoys)


# ---------------------------------------------------------------------------
# 공개 함수
# ---------------------------------------------------------------------------


def generate(products: list[Product], cfg: Config | None = None) -> SyntheticData:
    """배경 데이터부터 전부 합성으로 만들고 군집을 심는다."""
    cfg = cfg or Config()
    b = _Builder(cfg, products)
    videos = b.background()
    b.plant(videos)
    return b.result(pd.DataFrame(videos, columns=VIDEO_COLS))


def inject(
    videos: pd.DataFrame,
    channels: pd.DataFrame,
    comments: pd.DataFrame,
    products: list[Product],
    cfg: Config | None = None,
) -> SyntheticData:
    """이미 있는 표에 군집만 심는다. 표의 컬럼은 기본 컬럼 이름이어야 한다.

    주의: 실제 YouTube 데이터에 심은 결과는 내부 평가용으로만 쓰고 공개 자료에는 쓰지 않는다.
    """
    cfg = cfg or Config()
    missing = [c for c in ("video_id", "uploaded_at") if c not in videos.columns]
    if missing:
        raise ValueError(f"videos에 필요한 컬럼이 없습니다: {missing}")
    b = _Builder(cfg, products)
    # 기존 ID와 겹치지 않게
    if "comment_id" in comments:
        b._ids.update(comments["comment_id"].astype(str))
    if "channel_id" in channels:
        b._ids.update(channels["channel_id"].astype(str))
    vids = videos.copy()
    vids["uploaded_at"] = pd.to_datetime(vids["uploaded_at"], utc=True)
    b.plant(vids.to_dict("records"))
    return b.result(videos, channels, comments)


def _hash(value, key: bytes):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return value
    return hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()


def _iso(series: pd.Series) -> pd.Series:
    s = pd.to_datetime(series, utc=True)
    return s.dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def save(data: SyntheticData, out_dir: str | Path, cfg: Config) -> Path:
    """CSV 3개 + labels.json 저장. 시간은 UTC ISO 8601(Z)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    videos, channels, comments = data.videos.copy(), data.channels.copy(), data.comments.copy()
    clusters = json.loads(json.dumps(data.clusters))

    if cfg.hash_key:
        key = cfg.hash_key.encode()
        channels["channel_id"] = channels["channel_id"].map(lambda v: _hash(v, key))
        comments["author_channel_id"] = comments["author_channel_id"].map(lambda v: _hash(v, key))
        for c in clusters:
            c["account_ids"] = [_hash(a, key) for a in c["account_ids"]]

    videos["uploaded_at"] = _iso(videos["uploaded_at"])
    channels["channel_created_at"] = _iso(channels["channel_created_at"])
    comments["written_at"] = _iso(comments["written_at"])

    for name, df in (("videos", videos), ("channels", channels), ("comments", comments)):
        df.rename(columns=cfg.columns.get(name, {})).to_csv(out / f"{name}.csv", index=False, encoding="utf-8")

    meta = asdict(cfg)
    meta["start"] = cfg.start.isoformat()
    meta["hash_key"] = bool(cfg.hash_key)  # 키 자체는 저장하지 않는다
    (out / "labels.json").write_text(
        json.dumps({"config": meta, "clusters": clusters, "decoys": data.decoys}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out

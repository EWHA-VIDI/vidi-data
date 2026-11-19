"""VIDI 합성 군집 생성기.

실제 수집 데이터와 같은 모양(공통 규격 v1)의 videos / channels / comments 표를 만들고,
그 안에 "조직적 바이럴" 군집을 정답(labels)과 함께 심는다.

- generate(): 배경(일반 댓글)부터 전부 합성으로 만든다. 승인 전에도 쓸 수 있다.
- inject():  이미 있는 표(실제 수집 데이터 등)에 군집만 심는다.

정답은 labels.json에만 남기고 데이터 파일에는 넣지 않는다.
탐지 파이프라인은 데이터 파일만 읽고, 평가할 때만 labels.json과 맞춰 본다.
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

SCHEMA_VERSION = "v1"

# 공통 규격 v1 컬럼 (VIDI-45)
VIDEO_COLS = [
    "video_id",
    "channel_id",
    "title",
    "description",
    "published_at",
    "duration_seconds",
    "view_count",
    "like_count",
    "comment_count",
    "collected_at",
]
CHANNEL_COLS = ["channel_id", "published_at", "subscriber_count", "video_count", "view_count", "collected_at"]
COMMENT_COLS = [
    "comment_id",
    "video_id",
    "author_channel_id",
    "parent_comment_id",
    "text",
    "published_at",
    "updated_at",
    "like_count",
    "reply_count",
    "collected_at",
]
TIME_COLS = {
    "videos": ["published_at", "collected_at"],
    "channels": ["published_at", "collected_at"],
    "comments": ["published_at", "updated_at", "collected_at"],
}
INT_COLS = {
    "videos": ["duration_seconds", "view_count", "like_count", "comment_count"],
    "channels": ["subscriber_count", "video_count", "view_count"],
    "comments": ["like_count", "reply_count"],
}

# 난이도별 군집 특성
#   accounts: 계정 수, videos: 영상 수, hours: 업로드 후 몇 시간 안에 다는지,
#   fresh_ratio: 최근 생성 계정 비율, variation: 문장 변형 정도, reply_ratio: 서로 맞장구 답글 비율
DIFFICULTY = {
    "easy": dict(accounts=(8, 15), videos=(4, 8), hours=(0.2, 3), fresh_ratio=0.9, variation=0.2, reply_ratio=0.3),
    "medium": dict(accounts=(5, 10), videos=(3, 6), hours=(0.5, 12), fresh_ratio=0.6, variation=0.5, reply_ratio=0.15),
    "hard": dict(accounts=(3, 6), videos=(2, 4), hours=(1, 48), fresh_ratio=0.3, variation=0.8, reply_ratio=0.05),
}

# 키가 없을 때 쓰는 합성 전용 키. 실제 데이터에는 절대 쓰지 않는다.
SYNTHETIC_KEY = "vidi-synthetic-only"


@dataclass
class Config:
    seed: int = 42
    start: datetime = datetime(2026, 10, 1, tzinfo=timezone.utc)
    collected_at: datetime | None = None  # 없으면 마지막 업로드 + 14일
    # 배경 데이터 (generate에서만 사용)
    n_videos: int = 40
    upload_span_days: int = 14
    n_organic_accounts: int = 3000
    organic_comments_per_video: tuple[int, int] = (80, 300)
    mention_ratio: float = 0.35  # 일반 댓글 중 제품을 언급하는 비율
    organic_reply_ratio: float = 0.15
    edited_ratio: float = 0.03  # 수정된 댓글 비율 (updated_at)
    # 오탐 함정: 유명 제품을 자연스럽게 칭찬하는 댓글 묶음 (labels.decoys)
    n_decoys: int = 4
    # 심을 군집
    n_campaigns: int = 12
    difficulty_mix: dict[str, float] = field(default_factory=lambda: {"easy": 0.4, "medium": 0.4, "hard": 0.2})
    planted_tiers: tuple[str, ...] = ("중간", "저언급")
    # 저장 옵션
    pseudonym_key: str | None = None  # 없으면 SYNTHETIC_KEY
    pseudonym_key_version: str = "synthetic"
    run_id: str | None = None  # 없으면 collected_at 기준 run_YYYYMMDDTHHMMSSZ
    write_csv: bool = False  # 검수용 CSV도 함께 저장


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
        self.collected_at: datetime | None = cfg.collected_at

    # -- 기본 재료 ---------------------------------------------------------
    def new_id(self, prefix: str, n: int) -> str:
        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
        while True:
            value = prefix + "".join(self.rng.choice(chars) for _ in range(n))
            if value not in self._ids:
                self._ids.add(value)
                return value

    def add_channel(self, published_at: datetime, subscribers: int | None, videos: int | None = None) -> str:
        cid = self.new_id("UC", 22)
        self.channels.append(
            dict(
                channel_id=cid,
                published_at=published_at,
                subscriber_count=subscribers,
                video_count=videos if videos is not None else int(self.nrng.integers(0, 3)),
                view_count=int(self.nrng.integers(0, 500)),
            )
        )
        return cid

    def add_comment(self, video_id, author, text, published_at, likes, parent=None) -> str:
        cid = self.new_id("Ug", 24)
        if parent:
            cid = f"{parent}.{self.new_id('', 22)}"  # YouTube 답글 ID 형식: 부모ID.접미사
        edited = self.rng.random() < self.cfg.edited_ratio
        self.comments.append(
            dict(
                comment_id=cid,
                video_id=video_id,
                author_channel_id=author,
                parent_comment_id=parent,
                text=text,
                published_at=published_at,
                updated_at=published_at + timedelta(hours=self.rng.uniform(0.1, 48)) if edited else None,
                like_count=int(likes),
            )
        )
        return cid

    def pick_product(self, pool: list[Product]) -> Product:
        weights = np.array([p.weight for p in pool])
        return pool[int(self.nrng.choice(len(pool), p=weights / weights.sum()))]

    # -- 배경 --------------------------------------------------------------
    def background(self) -> list[dict]:
        cfg, rng, nrng = self.cfg, self.rng, self.nrng
        videos = []
        for _ in range(cfg.n_videos):
            uploader = self.add_channel(
                cfg.start - timedelta(days=int(nrng.integers(400, 3000))),
                int(nrng.integers(10_000, 2_000_000)),
                videos=int(nrng.integers(50, 800)),
            )
            videos.append(
                dict(
                    video_id=self.new_id("", 11),
                    channel_id=uploader,
                    title="[합성] 스킨케어 리뷰",
                    description=None,
                    published_at=cfg.start + timedelta(hours=rng.uniform(0, 24 * cfg.upload_span_days)),
                    duration_seconds=int(nrng.integers(180, 1500)),
                    view_count=int(nrng.integers(5_000, 500_000)),
                    like_count=int(nrng.integers(100, 10_000)),
                )
            )
        if self.collected_at is None:
            self.collected_at = max(v["published_at"] for v in videos) + timedelta(days=14)

        accounts = [
            self.add_channel(
                cfg.start - timedelta(days=int(nrng.integers(200, 3000))),
                None if rng.random() < 0.3 else int(nrng.integers(0, 50)),  # 비공개 구독자 수는 null
            )
            for _ in range(cfg.n_organic_accounts)
        ]
        for v in videos:
            lo, hi = cfg.organic_comments_per_video
            for _ in range(int(nrng.integers(lo, hi))):
                product = self.pick_product(self.products) if rng.random() < cfg.mention_ratio else None
                at = v["published_at"] + timedelta(hours=float(nrng.exponential(48)))
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
                    at = v["published_at"] + timedelta(hours=float(self.nrng.exponential(72)))
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
                rng.sample(videos, k=min(len(videos), rng.randint(*d["videos"]))), key=lambda v: v["published_at"]
            )
            accounts = []
            for _ in range(rng.randint(*d["accounts"])):
                fresh = rng.random() < d["fresh_ratio"]
                age = rng.randint(3, 60) if fresh else rng.randint(200, 2000)
                accounts.append(self.add_channel(targets[0]["published_at"] - timedelta(days=age), int(nrng.integers(0, 5)), videos=0))

            members: list[str] = []
            tops: list[tuple[str, dict, str, datetime]] = []
            for acc in accounts:
                # 계정마다 영상 1~3개에 댓글 → 같은 계정이 여러 영상에 반복 등장
                for v in rng.sample(targets, k=min(len(targets), rng.randint(1, 3))):
                    at = v["published_at"] + timedelta(hours=rng.uniform(*d["hours"]))
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
        new_ch = pd.DataFrame(self.channels, columns=CHANNEL_COLS)
        new_cm = pd.DataFrame(self.comments, columns=COMMENT_COLS)
        new_ch["collected_at"] = self.collected_at
        new_cm["collected_at"] = self.collected_at
        ch = new_ch if channels is None else pd.concat([channels, new_ch], ignore_index=True)
        cm = new_cm if comments is None else pd.concat([comments, new_cm], ignore_index=True)
        cm = _fill_reply_counts(cm)
        cm = cm.sample(frac=1, random_state=self.cfg.seed).reset_index(drop=True)
        return SyntheticData(videos=videos, channels=ch, comments=cm, clusters=self.clusters, decoys=self.decoys)


def _fill_reply_counts(cm: pd.DataFrame) -> pd.DataFrame:
    """최상위 댓글: 답글 수, 답글 행: null (규격 v1 3.3)."""
    counts = cm["parent_comment_id"].dropna().value_counts()
    top = cm["parent_comment_id"].isna()
    cm["reply_count"] = pd.array([pd.NA] * len(cm), dtype="Int64")
    cm.loc[top, "reply_count"] = cm.loc[top, "comment_id"].map(counts).fillna(0).astype("Int64")
    return cm


# ---------------------------------------------------------------------------
# 공개 함수
# ---------------------------------------------------------------------------


def generate(products: list[Product], cfg: Config | None = None) -> SyntheticData:
    """배경 데이터부터 전부 합성으로 만들고 군집을 심는다."""
    cfg = cfg or Config()
    b = _Builder(cfg, products)
    videos = b.background()
    b.plant(videos)
    vdf = pd.DataFrame(videos)
    vdf["comment_count"] = vdf["video_id"].map(pd.Series([c["video_id"] for c in b.comments]).value_counts()).fillna(0).astype(int)
    vdf["collected_at"] = b.collected_at
    return b.result(vdf[VIDEO_COLS])


def inject(
    videos: pd.DataFrame,
    channels: pd.DataFrame,
    comments: pd.DataFrame,
    products: list[Product],
    cfg: Config | None = None,
) -> SyntheticData:
    """이미 있는 규격 v1 표에 군집만 심는다.

    이미 가명화된 ID(ch_, cm_)는 저장할 때 다시 바꾸지 않는다.
    주의: 실제 YouTube 데이터에 심은 결과는 내부 평가용으로만 쓰고 공개 자료에는 쓰지 않는다.
    """
    cfg = cfg or Config()
    missing = [c for c in ("video_id", "published_at") if c not in videos.columns]
    if missing:
        raise ValueError(f"videos에 필요한 컬럼이 없습니다: {missing}")
    b = _Builder(cfg, products)
    if "comment_id" in comments:
        b._ids.update(comments["comment_id"].astype(str))
    if "channel_id" in channels:
        b._ids.update(channels["channel_id"].astype(str))
    if b.collected_at is None and "collected_at" in comments and len(comments):
        b.collected_at = pd.to_datetime(comments["collected_at"], utc=True).max().to_pydatetime()
    vids = videos.copy()
    vids["published_at"] = pd.to_datetime(vids["published_at"], utc=True)
    if b.collected_at is None:
        b.collected_at = vids["published_at"].max().to_pydatetime() + timedelta(days=14)
    b.plant(vids.to_dict("records"))
    return b.result(videos, channels, comments)


# ---------------------------------------------------------------------------
# 저장
# ---------------------------------------------------------------------------


def pseudonymize(value, kind: str, key: bytes):
    """규격 v1 4장: ch_/cm_ + HMAC-SHA256(키, 'channel:'/'comment:' + 원본 ID) 소문자 hex 64자."""
    prefix = {"channel": "ch_", "comment": "cm_"}[kind]
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NA:
        return None
    value = str(value)
    if value.startswith(prefix):  # 이미 가명화된 값 (inject한 실제 데이터)
        return value
    return prefix + hmac.new(key, f"{kind}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()


def _apply_pseudonyms(data: SyntheticData, key: bytes) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict], list[dict]]:
    ch_ = lambda v: pseudonymize(v, "channel", key)  # noqa: E731
    cm_ = lambda v: pseudonymize(v, "comment", key)  # noqa: E731
    videos, channels, comments = data.videos.copy(), data.channels.copy(), data.comments.copy()
    videos["channel_id"] = videos["channel_id"].map(ch_)
    channels["channel_id"] = channels["channel_id"].map(ch_)
    comments["author_channel_id"] = comments["author_channel_id"].map(ch_)
    comments["comment_id"] = comments["comment_id"].map(cm_)
    comments["parent_comment_id"] = comments["parent_comment_id"].map(cm_)
    clusters = [
        {**c, "account_ids": [ch_(a) for a in c["account_ids"]], "comment_ids": [cm_(i) for i in c["comment_ids"]]}
        for c in data.clusters
    ]
    decoys = [{**d, "comment_ids": [cm_(i) for i in d["comment_ids"]]} for d in data.decoys]
    return videos, channels, comments, clusters, decoys


def _typed(df: pd.DataFrame, name: str, cols: list[str]) -> pd.DataFrame:
    df = df.reindex(columns=cols)
    for c in TIME_COLS[name]:
        df[c] = pd.to_datetime(df[c], utc=True).astype("datetime64[us, UTC]")
    for c in INT_COLS[name]:
        df[c] = pd.to_numeric(df[c]).astype("Int64")
    for c in df.columns:
        if c not in TIME_COLS[name] and c not in INT_COLS[name]:
            df[c] = df[c].astype("string")
    return df


def save(data: SyntheticData, out_dir: str | Path, cfg: Config) -> Path:
    """규격 v1 형식으로 저장: videos/channels/comments.parquet(zstd) + manifest.json + labels.json."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    key = (cfg.pseudonym_key or SYNTHETIC_KEY).encode("utf-8")
    videos, channels, comments, clusters, decoys = _apply_pseudonyms(data, key)

    tables = {
        "videos": _typed(videos, "videos", VIDEO_COLS),
        "channels": _typed(channels.drop_duplicates("channel_id"), "channels", CHANNEL_COLS),
        "comments": _typed(comments, "comments", COMMENT_COLS),
    }
    for name, df in tables.items():
        df.to_parquet(out / f"{name}.parquet", compression="zstd", index=False)
        if cfg.write_csv:
            csv = df.copy()
            for c in TIME_COLS[name]:
                csv[c] = csv[c].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            csv.to_csv(out / f"{name}.csv", index=False, encoding="utf-8")

    collected = pd.Timestamp(tables["comments"]["collected_at"].max())
    run_id = cfg.run_id or f"run_{collected:%Y%m%dT%H%M%SZ}"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "synthetic": True,
        "pseudonym_key_version": cfg.pseudonym_key_version if cfg.pseudonym_key else "synthetic",
        "collected_at": f"{collected:%Y-%m-%dT%H:%M:%SZ}",
        "row_counts": {name: len(df) for name, df in tables.items()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = asdict(cfg)
    for k in ("start", "collected_at"):
        meta[k] = meta[k].isoformat() if meta[k] else None
    meta["pseudonym_key"] = bool(cfg.pseudonym_key)  # 키 자체는 저장하지 않는다
    (out / "labels.json").write_text(
        json.dumps({"config": meta, "clusters": clusters, "decoys": decoys}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out

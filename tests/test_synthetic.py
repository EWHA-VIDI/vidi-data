import hashlib
import hmac
import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from src.synthetic import Config, generate, inject, load_products, save
from src.synthetic.generator import CHANNEL_COLS, COMMENT_COLS, VIDEO_COLS, pseudonymize

FIXTURE = Path(__file__).parent / "fixtures" / "product_dict_small.json"


@pytest.fixture(scope="module")
def products():
    return load_products(FIXTURE)


@pytest.fixture(scope="module")
def small_cfg():
    return Config(n_videos=10, n_organic_accounts=300, organic_comments_per_video=(20, 40), n_campaigns=5, n_decoys=2)


@pytest.fixture(scope="module")
def data(products, small_cfg):
    return generate(products, small_cfg)


@pytest.fixture(scope="module")
def saved(tmp_path_factory, data, small_cfg):
    out = save(data, tmp_path_factory.mktemp("run"), small_cfg)
    read = lambda n: pd.read_parquet(out / f"{n}.parquet")  # noqa: E731
    labels = json.loads((out / "labels.json").read_text(encoding="utf-8"))
    return out, read("videos"), read("channels"), read("comments"), labels


def test_같은_seed면_같은_결과(products, small_cfg):
    a, b = generate(products, small_cfg), generate(products, small_cfg)
    pd.testing.assert_frame_equal(a.comments, b.comments)


def test_정답_댓글이_모두_표에_있다(data):
    ids = set(data.comments.comment_id)
    for c in data.clusters:
        assert set(c["comment_ids"]) <= ids
        authors = set(data.comments[data.comments.comment_id.isin(c["comment_ids"])].author_channel_id)
        assert authors <= set(c["account_ids"])


def test_ID가_겹치지_않는다(data):
    assert data.comments.comment_id.is_unique
    assert data.channels.channel_id.is_unique


def test_답글_표현이_규격과_같다(data):
    c = data.comments
    replies = c[c.parent_comment_id.notna()]
    tops = c[c.parent_comment_id.isna()]
    assert len(replies) > 0
    assert set(replies.parent_comment_id) <= set(c.comment_id)
    assert replies.reply_count.isna().all()  # 답글 행은 null
    expected = replies.parent_comment_id.value_counts()
    got = tops.set_index("comment_id").reply_count
    assert (got.loc[expected.index] == expected).all()


def test_유명_제품은_심지_않는다(data, products):
    famous = {p.product_id for p in products if p.tier == "유명"}
    assert all(c["product_id"] not in famous for c in data.clusters)
    assert all(d["product_id"] in famous for d in data.decoys)


def test_군집_댓글은_업로드_직후에_몰린다(data):
    uploaded = data.videos.set_index("video_id").published_at
    c = data.comments
    c = c[c.parent_comment_id.isna()]
    planted = {i for cl in data.clusters for i in cl["comment_ids"]}
    delay = (pd.to_datetime(c.published_at, utc=True) - c.video_id.map(uploaded)).dt.total_seconds() / 3600
    assert delay[c.comment_id.isin(planted)].median() < delay[~c.comment_id.isin(planted)].median()


def test_저장_컬럼과_타입이_규격_v1과_같다(saved):
    out, videos, channels, comments, _ = saved
    assert list(videos.columns) == VIDEO_COLS
    assert list(channels.columns) == CHANNEL_COLS
    assert list(comments.columns) == COMMENT_COLS
    for df in (videos, channels, comments):
        assert str(df.published_at.dtype) == "datetime64[us, UTC]"
        assert str(df.collected_at.dtype) == "datetime64[us, UTC]"
    assert comments.like_count.dtype.name in ("Int64", "int64")
    assert {"is_synthetic_campaign", "content", "written_at"}.isdisjoint(comments.columns)  # 정답·옛 이름 없음


def test_업로더와_작성자가_같은_채널_표에_있다(saved):
    _, videos, channels, comments, _ = saved
    ids = set(channels.channel_id)
    assert set(videos.channel_id) <= ids
    assert set(comments.author_channel_id.dropna()) <= ids


def test_ID_가명화_규칙(saved):
    _, videos, channels, comments, labels = saved
    assert channels.channel_id.str.fullmatch(r"ch_[0-9a-f]{64}").all()
    assert comments.comment_id.str.fullmatch(r"cm_[0-9a-f]{64}").all()
    assert comments.parent_comment_id.dropna().str.fullmatch(r"cm_[0-9a-f]{64}").all()
    assert videos.video_id.str.len().eq(11).all()  # 영상 ID는 원문 유지
    cl = labels["clusters"][0]
    assert set(cl["comment_ids"]) <= set(comments.comment_id)
    assert set(cl["account_ids"]) <= set(channels.channel_id)


def test_가명화_함수는_규격_공식과_같다():
    key = b"k"
    expected = "ch_" + hmac.new(key, b"channel:UCabc", hashlib.sha256).hexdigest()
    assert pseudonymize("UCabc", "channel", key) == expected
    assert pseudonymize(expected, "channel", key) == expected  # 이미 가명화된 값은 그대로
    assert pseudonymize(None, "comment", key) is None


def test_manifest에_키를_남기지_않는다(tmp_path, data, small_cfg):
    cfg = replace(small_cfg, pseudonym_key="secret-key", pseudonym_key_version="v1")
    out = save(data, tmp_path, cfg)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "v1"
    assert manifest["pseudonym_key_version"] == "v1"
    for f in ("manifest.json", "labels.json"):
        assert "secret-key" not in (out / f).read_text(encoding="utf-8")


def test_검수용_CSV_옵션(tmp_path, data, small_cfg):
    out = save(data, tmp_path, replace(small_cfg, write_csv=True))
    csv = pd.read_csv(out / "comments.csv")
    assert csv.published_at.str.endswith("Z").all()


def test_inject는_기존_데이터를_유지하고_다시_가명화하지_않는다(saved, products):
    _, videos, channels, comments, _ = saved
    injected = inject(videos, channels, comments, products, Config(seed=7, n_campaigns=3))
    assert set(comments.comment_id) <= set(injected.comments.comment_id)
    assert len(injected.clusters) == 3
    assert injected.comments.comment_id.is_unique


def test_심을_제품이_없으면_유명_제품으로_대신하지_않는다(products, small_cfg):
    with pytest.raises(ValueError):
        generate(products, replace(small_cfg, planted_tiers=("없는등급",)))


def test_군집_계정이_구독자_수나_계정_나이만으로_드러나지_않는다(data):
    ch = data.channels.set_index("channel_id")
    planted = {a for c in data.clusters for a in c["account_ids"]}
    commenters = set(data.comments.author_channel_id)
    organic = list(commenters - planted)
    # 구독자 수: 같은 분포 (0~49, 일부 null)
    assert ch.loc[list(planted), "subscriber_count"].dropna().max() > 4
    # 계정 나이: 일반 계정에도 최근 생성 계정이 섞여 있다
    ref = data.videos.published_at.min()
    age = (ref - pd.to_datetime(ch.loc[organic, "published_at"], utc=True)).dt.days
    assert (age < 60).any()

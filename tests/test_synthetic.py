import json
from pathlib import Path

import pandas as pd
import pytest

from src.synthetic import Config, generate, inject, load_products, save
from src.synthetic.generator import COMMENT_COLS

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


def test_답글의_부모가_존재한다(data):
    replies = data.comments.dropna(subset=["parent_comment_id"])
    assert len(replies) > 0
    assert set(replies.parent_comment_id) <= set(data.comments.comment_id)


def test_유명_제품은_심지_않는다(data, products):
    famous = {p.product_id for p in products if p.tier == "유명"}
    assert all(c["product_id"] not in famous for c in data.clusters)
    assert all(d["product_id"] in famous for d in data.decoys)


def test_군집_댓글은_업로드_직후에_몰린다(data):
    uploaded = data.videos.set_index("video_id").uploaded_at
    c = data.comments
    c = c[c.parent_comment_id.isna()]
    planted = {i for cl in data.clusters for i in cl["comment_ids"]}
    delay = (c.written_at - c.video_id.map(uploaded)).dt.total_seconds() / 3600
    assert delay[c.comment_id.isin(planted)].median() < delay[~c.comment_id.isin(planted)].median()


def test_save는_정답을_CSV에_넣지_않는다(tmp_path, data, small_cfg):
    out = save(data, tmp_path, small_cfg)
    comments = pd.read_csv(out / "comments.csv")
    assert list(comments.columns) == COMMENT_COLS
    assert comments.written_at.str.endswith("Z").all()
    labels = json.loads((out / "labels.json").read_text(encoding="utf-8"))
    assert len(labels["clusters"]) == small_cfg.n_campaigns


def test_hash_key면_계정ID가_64자_해시(tmp_path, data, small_cfg):
    cfg = Config(**{**small_cfg.__dict__, "hash_key": "test-key"})
    out = save(data, tmp_path, cfg)
    channels = pd.read_csv(out / "channels.csv")
    assert channels.channel_id.str.len().eq(64).all()
    labels = json.loads((out / "labels.json").read_text(encoding="utf-8"))
    assert labels["config"]["hash_key"] is True  # 키 원문은 저장 안 함
    assert set(labels["clusters"][0]["account_ids"]) <= set(channels.channel_id)


def test_컬럼_이름_바꿔_저장(tmp_path, data, small_cfg):
    cfg = Config(**{**small_cfg.__dict__, "columns": {"comments": {"content": "text"}}})
    out = save(data, tmp_path, cfg)
    assert "text" in pd.read_csv(out / "comments.csv").columns


def test_inject는_기존_데이터를_유지한다(data, products):
    base_ids = set(data.comments.comment_id)
    cfg = Config(seed=7, n_campaigns=3)
    injected = inject(data.videos, data.channels, data.comments, products, cfg)
    assert base_ids <= set(injected.comments.comment_id)
    assert len(injected.clusters) == 3
    assert injected.comments.comment_id.is_unique

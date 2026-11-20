"""01 문서 4장 영상 조건.

조건은 영상 하나를 보고 "이 샤드가 수집할 수 있는가"만 판정한다.
어느 샤드가 실제로 수집할지(순서, 채널당 상한)는 merge.py가 정한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# 01 문서 1장 하위 카테고리
CATEGORY_WORDS = {
    "toner": ["토너", "스킨", "닦토"],
    "serum": ["앰플", "세럼", "에센스", "serum", "ampoule"],
    "cream": ["크림", "cream"],
    "sun": ["선크림", "썬크림", "선세럼", "썬세럼", "선스틱", "썬스틱", "선케어", "썬케어", "자외선", "sunscreen"],
}
# "스킨"이 "스킨케어" 안에서 잡히지 않게 먼저 지울 말
CATEGORY_STOPWORDS = ["스킨케어", "skincare", "스킨부스터", "스킨보톡스"]

# 기준선 영상 종류 (01 문서 4장). 자동으로 거르지 않고 표시만 한다
VIDEO_TYPE_WORDS = {
    "review": ["리뷰", "후기", "솔직", "써봤", "사용기"],
    "compare": ["비교", "순위", "티어", "랭킹", "best", "베스트", "top"],
    "recommend": ["추천", "꿀템", "인생템"],
    "routine": ["루틴", "routine"],
    "own_money": ["내돈내산"],
    "haul": ["하울", "haul"],
}

# 고민 샤드(1·2) 제외 후보 1차 표시 (01 문서 4장). 표시된 영상만 사람이 확인
EXCLUDE_HINT_WORDS = ["시술", "레이저", "압출", "이벤트", "예약", "연고", "만들기", "diy", "DIY"]

_DURATION = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
_HANGUL = re.compile(r"[가-힣]")


def parse_duration(value: str | None) -> int | None:
    """ISO 8601 길이(PT1H2M3S) → 초. 라이브 예정 등 값이 없으면 None."""
    if not value:
        return None
    m = _DURATION.match(value)
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def kst(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=KST)


def _matches(text: str, words: list[str]) -> list[str]:
    t = text.lower()
    return [w for w in words if w.lower() in t]


def categories_of(text: str) -> list[str]:
    t = text.lower()
    for w in CATEGORY_STOPWORDS:
        t = t.replace(w.lower(), " ")
    return [code for code, words in CATEGORY_WORDS.items() if any(w.lower() in t for w in words)]


def video_types_of(text: str) -> list[str]:
    return [code for code, words in VIDEO_TYPE_WORDS.items() if _matches(text, words)]


@dataclass(frozen=True)
class ShardRule:
    """한 샤드(또는 샤드 2의 비립종/좁쌀)의 조건. end는 그날 하루 끝까지 포함하도록 다음날 0시(미포함)."""

    name: str
    start: datetime
    end: datetime
    min_comments: int
    channel_cap: int
    required_words: tuple[str, ...] = ()  # 제목·설명에 하나 이상
    need_category: bool = False  # 기준선: 하위 카테고리 하나 이상


RULES = {
    "baseline": ShardRule("baseline", kst(2025, 11, 1), kst(2026, 11, 12), 50, 25, need_category=True),
    "milia": ShardRule("milia", kst(2021, 1, 1), kst(2026, 11, 12), 10, 5, ("비립종",)),
    "comedone": ShardRule("comedone", kst(2024, 1, 1), kst(2026, 11, 12), 50, 5, ("좁쌀",)),
    "acne": ShardRule("acne", kst(2024, 1, 1), kst(2026, 11, 12), 50, 5, ("여드름", "트러블")),
}
DEFER_COMMENTS = 5_000  # 이 이상은 맨 뒤로 미룸 (임시값)
SHORT_LIKE_SEC = 180


def text_of(video: dict) -> str:
    sn = video.get("snippet", {})
    return f"{sn.get('title', '')}\n{sn.get('description', '')}"


def comment_count(video: dict) -> int | None:
    value = video.get("statistics", {}).get("commentCount")
    return int(value) if value is not None else None


def check(video: dict, rule: ShardRule) -> list[str]:
    """탈락 사유 목록. 비어 있으면 통과. 채널당 상한은 여기서 보지 않는다."""
    sn = video.get("snippet", {})
    text = text_of(video)
    reasons = []
    published = parse_time(sn["publishedAt"])
    if not (rule.start <= published < rule.end):
        reasons.append("기간 밖")
    comments = comment_count(video)
    if not comments:
        reasons.append("댓글 비활성화")
    elif comments < rule.min_comments:
        reasons.append(f"댓글 {rule.min_comments}개 미만")
    if not _HANGUL.search(text):
        reasons.append("한국어 아님")
    if rule.required_words and not _matches(text, list(rule.required_words)):
        reasons.append("필수 단어 없음(" + "/".join(rule.required_words) + ")")
    if rule.need_category and not categories_of(text):
        reasons.append("하위 카테고리 없음")
    if sn.get("liveBroadcastContent") in ("live", "upcoming"):
        reasons.append("라이브 진행·예정")
    return reasons


def shard2_rule(video: dict) -> ShardRule:
    """01 문서 4장: 제목·설명에 '비립종'이 있으면 비립종 조건, 없으면 좁쌀 조건."""
    return RULES["milia"] if "비립종" in text_of(video) else RULES["comedone"]


@dataclass
class VideoInfo:
    """마스터 목록에 기록하는 영상 정보 (조건 판정과 별개)."""

    video_id: str
    channel_id: str
    channel_title: str
    title: str
    published_at: str
    categories: list[str]
    video_types: list[str]
    exclude_hints: list[str]
    has_paid_placement: bool | None
    duration_sec: int | None
    short_like: bool | None
    comment_count: int | None
    defer: bool
    extra: dict = field(default_factory=dict)


def info_of(video: dict) -> VideoInfo:
    sn, cd = video.get("snippet", {}), video.get("contentDetails", {})
    text = text_of(video)
    duration = parse_duration(cd.get("duration"))
    comments = comment_count(video)
    return VideoInfo(
        video_id=video["id"],
        channel_id=sn.get("channelId"),
        channel_title=sn.get("channelTitle"),
        title=sn.get("title"),
        published_at=parse_time(sn["publishedAt"]).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        categories=categories_of(text),
        video_types=video_types_of(text),
        exclude_hints=_matches(text, EXCLUDE_HINT_WORDS),
        has_paid_placement=video.get("paidProductPlacementDetails", {}).get("hasPaidProductPlacement"),
        duration_sec=duration,
        short_like=None if duration is None else duration <= SHORT_LIKE_SEC,
        comment_count=comments,
        defer=comments is not None and comments >= DEFER_COMMENTS,
    )

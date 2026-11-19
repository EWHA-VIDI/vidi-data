"""YouTube API 응답 → 규격 v1 행. API 호출·페이지네이션은 수집기가 담당한다."""

from datetime import datetime
from typing import Protocol

from .schema import utc


class IdEncoder(Protocol):
    def channel_id(self, raw_id: str | None) -> str | None: ...
    def comment_id(self, raw_id: str | None) -> str | None: ...


def _count(stats, key):
    value = stats.get(key)
    return None if value is None else int(value)


class YouTubeRows:
    def __init__(self, ids: IdEncoder, collected_at: str | datetime):
        self.ids = ids
        self.collected_at = utc(collected_at)

    def video(self, resource: dict, *, duration_seconds: int | None) -> dict:
        snippet, stats = resource["snippet"], resource.get("statistics", {})
        return {
            "video_id": resource["id"],
            "channel_id": self.ids.channel_id(snippet["channelId"]),
            "title": snippet["title"],
            "description": snippet.get("description"),
            "published_at": utc(snippet["publishedAt"]),
            "duration_seconds": duration_seconds,
            "view_count": _count(stats, "viewCount"),
            "like_count": _count(stats, "likeCount"),
            "comment_count": _count(stats, "commentCount"),
            "collected_at": self.collected_at,
        }

    def channel(self, raw_id: str, resource: dict | None) -> dict:
        # 삭제·조회 불가 채널도 참조 행을 남기고 알 수 없는 통계를 0으로 바꾸지 않는다.
        if resource is not None and resource["id"] != raw_id:
            raise ValueError("요청한 채널과 응답이 일치하지 않습니다")
        snippet = resource.get("snippet", {}) if resource else {}
        stats = resource.get("statistics", {}) if resource else {}
        published = snippet.get("publishedAt")
        return {
            "channel_id": self.ids.channel_id(raw_id),
            "published_at": utc(published) if published else None,
            "subscriber_count": None
            if stats.get("hiddenSubscriberCount", True)
            else _count(stats, "subscriberCount"),
            "video_count": _count(stats, "videoCount"),
            "view_count": _count(stats, "viewCount"),
            "collected_at": self.collected_at,
        }

    def comment(self, resource: dict, *, total_reply_count: int | None = None) -> dict:
        snippet = resource["snippet"]
        parent = snippet.get("parentId")
        author = snippet.get("authorChannelId") or {}
        updated = snippet.get("updatedAt")
        return {
            "comment_id": self.ids.comment_id(resource["id"]),
            "video_id": snippet["videoId"],
            "author_channel_id": self.ids.channel_id(author.get("value")),
            "parent_comment_id": self.ids.comment_id(parent),
            "text": snippet["textDisplay"],
            "published_at": utc(snippet["publishedAt"]),
            "updated_at": utc(updated) if updated else None,
            "like_count": _count(snippet, "likeCount"),
            "reply_count": None if parent else total_reply_count,
            "collected_at": self.collected_at,
        }

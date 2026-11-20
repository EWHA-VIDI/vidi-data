"""YouTube Data API 호출. 표준 라이브러리만 쓴다.

할당량(2026-11 공식 문서 기준)
- search.list: 프로젝트당 하루 100회, 1회 1단위로 따로 셈
- 그 밖의 호출(channels, playlistItems, videos 등): 하루 10,000단위, 1회 1단위
- 초기화: 태평양 시간 자정 (11월은 한국 시간 오후 5시)
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from .rules import parse_time

API = "https://www.googleapis.com/youtube/v3"


class QuotaExceeded(RuntimeError):
    pass


class YouTube:
    def __init__(self, api_key: str, *, opener=urllib.request.urlopen, sleep=time.sleep):
        self.key = api_key
        self.units = 0  # search 제외 호출 수
        self.search_calls = 0
        self._open = opener
        self._sleep = sleep

    def get(self, resource: str, **params) -> dict:
        query = urllib.parse.urlencode({**params, "key": self.key})
        url = f"{API}/{resource}?{query}"
        for attempt in range(3):
            try:
                if resource == "search":
                    self.search_calls += 1
                else:
                    self.units += 1
                with self._open(url, timeout=30) as res:
                    return json.loads(res.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                if e.code in (403, 429) and ("quotaExceeded" in body or "rateLimitExceeded" in body):
                    raise QuotaExceeded(
                        f"{resource} 할당량을 다 썼습니다. 한국 시간 오후 5시 이후 같은 명령으로 다시 실행하면 이어서 합니다."
                    ) from None
                if e.code in (500, 503) and attempt < 2:
                    self._sleep(2**attempt)
                    continue
                # 키 값이 에러 메시지에 남지 않게 URL은 출력하지 않는다
                raise RuntimeError(f"{resource} 호출 실패 ({e.code}): {body[:300]}") from None
        raise RuntimeError(f"{resource} 호출 실패")

    def uploads_playlists(self, channel_ids: list[str]) -> dict[str, str]:
        out = {}
        for i in range(0, len(channel_ids), 50):
            res = self.get("channels", part="contentDetails", id=",".join(channel_ids[i : i + 50]), maxResults=50)
            for item in res.get("items", []):
                out[item["id"]] = item["contentDetails"]["relatedPlaylists"]["uploads"]
        return out

    def recent_video_ids(self, playlist_id: str, since: datetime, max_pages: int) -> list[str]:
        """업로드 목록은 최신순. 한 페이지가 통째로 기간 시작보다 오래되면 멈춘다."""
        ids, token = [], None
        for _ in range(max_pages):
            params = dict(part="contentDetails", playlistId=playlist_id, maxResults=50)
            if token:
                params["pageToken"] = token
            res = self.get("playlistItems", **params)
            page_new = False
            for item in res.get("items", []):
                published = item["contentDetails"].get("videoPublishedAt")
                if published and parse_time(published) >= since:
                    ids.append(item["contentDetails"]["videoId"])
                    page_new = True
            token = res.get("nextPageToken")
            if not token or not page_new:
                break
        return ids

    def videos(self, video_ids: list[str]) -> list[dict]:
        out = []
        ids = list(dict.fromkeys(video_ids))
        for i in range(0, len(ids), 50):
            res = self.get(
                "videos",
                part="snippet,statistics,contentDetails,paidProductPlacementDetails",
                id=",".join(ids[i : i + 50]),
            )
            out.extend(res.get("items", []))
        return out

    def search_page(self, keyword: str, published_after: str, published_before: str, page_token: str | None) -> dict:
        """01 문서 7장 공통 파라미터. order는 기본값(relevance)."""
        params = dict(
            part="id",
            q=keyword,
            type="video",
            regionCode="KR",
            relevanceLanguage="ko",
            maxResults=50,
            publishedAfter=published_after,
            publishedBefore=published_before,
        )
        if page_token:
            params["pageToken"] = page_token
        return self.get("search", **params)

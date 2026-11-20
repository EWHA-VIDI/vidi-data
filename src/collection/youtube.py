"""공개 Data API 클라이언트. 원본 응답·키·요청 URL을 로그로 남기지 않는다."""

import fcntl
import json
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


class APIError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class QuotaStop(APIError):
    pass


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Budget:
    """한 Google 프로젝트의 일일 호출 예산. 모든 키·샤드에서 같은 파일 사용."""

    def __init__(self, path, limit=8000, clock=lambda: datetime.now(UTC)):
        if not 1 <= limit <= 8000:
            raise ValueError("호출 예산은 1~8000이어야 합니다")
        self.path, self.limit, self.clock = Path(path), limit, clock

    def take(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            day = (
                self.clock()
                .astimezone(ZoneInfo("America/Los_Angeles"))
                .date()
                .isoformat()
            )
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            used = state.get("used", 0) if state.get("date") == day else 0
            if used >= self.limit:
                raise QuotaStop("localBudgetExceeded")
            atomic_json(self.path, {"date": day, "used": used + 1})


class YouTubeClient:
    def __init__(
        self,
        key,
        budget,
        *,
        opener=urlopen,
        sleep=time.sleep,
        clock=lambda: datetime.now(UTC),
    ):
        if not key:
            raise ValueError("YOUTUBE_API_KEY가 필요합니다")
        self._key, self.budget = key, budget
        self.opener, self.sleep, self.clock = opener, sleep, clock

    def get(self, endpoint, **parameters):
        if endpoint not in {"videos", "channels", "commentThreads", "comments"}:
            raise ValueError("수집 단계에서는 검색 API를 사용하지 않습니다")
        request = Request(
            "https://www.googleapis.com/youtube/v3/"
            + endpoint
            + "?"
            + urlencode(parameters),
            headers={"X-Goog-Api-Key": self._key, "Accept": "application/json"},
        )
        for attempt in range(4):
            self.budget.take()  # 실패·재시도도 각각 예산에 포함한다.
            try:
                with self.opener(request, timeout=30) as response:
                    return json.load(response)
            except HTTPError as error:
                try:
                    reason = (
                        json.loads(error.read())
                        .get("error", {})
                        .get("errors", [{}])[0]
                        .get("reason")
                    )
                except (ValueError, IndexError, AttributeError):
                    reason = None
                if error.code == 401 or reason in {
                    "keyInvalid",
                    "accessNotConfigured",
                    "ipRefererBlocked",
                }:
                    raise QuotaStop("authenticationRejected") from None
                if reason in {"quotaExceeded", "dailyLimitExceeded"}:
                    raise QuotaStop(reason) from None
                if reason in {"commentsDisabled", "videoNotFound"}:
                    raise APIError(reason) from None
                retryable = error.code in {429, 500, 502, 503, 504} or reason in {
                    "rateLimitExceeded",
                    "userRateLimitExceeded",
                }
                if not retryable:
                    raise APIError("http" + str(error.code)) from None
            except (URLError, TimeoutError, OSError):
                pass
            except ValueError:
                raise APIError("invalidJSON") from None
            if attempt == 3:
                raise APIError("retryExhausted") from None
            self.sleep(2**attempt)

    def pages(self, endpoint, **parameters):
        seen = set()
        while True:
            data = self.get(endpoint, **parameters)
            yield data
            token = data.get("nextPageToken")
            if not token:
                return
            if token in seen:
                raise APIError("repeatedPageToken")
            seen.add(token)
            parameters["pageToken"] = token

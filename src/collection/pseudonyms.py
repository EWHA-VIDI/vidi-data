"""수집 직후 사용하는 공통 HMAC 규칙; 원본-ID 매핑은 저장하지 않는다."""

from dataclasses import dataclass, field
import hashlib
import hmac
import os
import re


@dataclass(frozen=True)
class Pseudonymizer:
    key: bytes = field(repr=False)
    key_version: str = "v1"

    def __post_init__(self) -> None:
        if not isinstance(self.key, bytes) or len(self.key) < 32:
            raise ValueError("가명화 키는 32바이트 이상이어야 합니다")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", self.key_version):
            raise ValueError("키 버전 형식이 올바르지 않습니다")

    @classmethod
    def from_environment(cls) -> "Pseudonymizer":
        value = os.environ.get("DATA_PSEUDONYM_KEY")
        if not value:
            raise ValueError("DATA_PSEUDONYM_KEY를 안전하게 주입해야 합니다")
        return cls(value.encode("utf-8"), os.environ.get("DATA_PSEUDONYM_KEY_VERSION", "v1"))

    def _digest(self, raw_id: str | None, kind: str, prefix: str) -> str | None:
        if raw_id is None:
            return None
        if not isinstance(raw_id, str) or not raw_id or raw_id.strip() != raw_id:
            raise ValueError("원본 ID는 비어 있지 않은 문자열이어야 합니다")
        if raw_id.startswith(("ch_", "cm_")):
            raise ValueError("이미 가명화된 ID를 원본으로 다시 처리하지 않습니다")
        message = f"{kind}:{raw_id}".encode("utf-8")
        return prefix + hmac.new(self.key, message, hashlib.sha256).hexdigest()

    def channel_id(self, raw_id: str | None) -> str | None:
        return self._digest(raw_id, "channel", "ch_")

    def comment_id(self, raw_id: str | None) -> str | None:
        return self._digest(raw_id, "comment", "cm_")

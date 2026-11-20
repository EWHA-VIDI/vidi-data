"""샤드 3 기준선 채널 16곳의 기간 안 영상 모으기 (01 문서 7.3장).

    채널 16곳 → channels.list(업로드 재생목록) → playlistItems.list(기간 안 영상) → videos.list
"""

from __future__ import annotations

from .rules import RULES
from .youtube import YouTube

# 01 문서 7.3장. 11/19 고정, 바꾸려면 팀 합의와 변경 기록 필요
BASELINE_CHANNELS = {
    "UCqrNqg3UgVoD3Sa-F_TxuSA": "디렉터파이",
    "UCAVIfs9U_yhPL234y2V2e4g": "옌마드Yenmad",
    "UCoMNFh_1SZ5-qrkktzdSPGA": "프롬베이지",
    "UCmYFgly9rKU6VEsNT6J1DMQ": "피부에진심인 써니",
    "UCzB4SMiJegcyFlnFvosUVcg": "뷰티부장관",
    "UChaTUCM7bPBTJaIZQ83PNwQ": "스완SWAN_현실남자관리",
    "UCvGvf3Lk0Z5reU544g_SzPg": "비트 BTE",
    "UCbgx2UTKb56ICoDpuaix2tQ": "본질남",
    "UC5o9Z5lgw4kk4fjDhweZxKg": "피부왕정윤",
    "UCT6W2AUy8If3fN1OoKIFUsg": "박경열의 피부상담소",
    "UCvmrt-GOa-bJt_0aCd8z1Fg": "지연쌤의 피부참견",
    "UCUH_M0bX4f1JZns0WdBJVKQ": "피부심 심현철",
    "UC120Dx91wwLlbOjxCMBpZiQ": "빛나는 피부 닥터영",
    "UC0TR0Ciw0rQSJzgU5NiwvRQ": "동네 의사 이상욱",
    "UC-hvam4FHeXJDQZ5sCW132A": "청담 닥터원",
    "UCRhACfjsdpVQZCn89Ya5MsQ": "유진스 Eugenes Skin",
}


def fetch_baseline(yt: YouTube, channels: dict[str, str] = BASELINE_CHANNELS, max_pages: int = 10) -> dict:
    """기준선 채널의 기간 안 영상 리소스(videos.list 원본)를 모은다."""
    uploads = yt.uploads_playlists(list(channels))
    ids = []
    for cid in channels:
        if cid in uploads:
            ids.extend(yt.recent_video_ids(uploads[cid], RULES["baseline"].start, max_pages))
    return dict(videos=yt.videos(ids), missing_channels=[c for c in channels if c not in uploads])

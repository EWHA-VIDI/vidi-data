"""댓글 문장 재료.

조직적 바이럴 댓글은 "고민 → 제품 → 개선 → 권유" 구조가 반복된다.
일반 댓글은 구조가 제각각이고 제품 언급이 없거나 중립적이다.
"""

from __future__ import annotations

import random

from .products import Product

# 제품 사전 concerns 코드 → 댓글에서 쓰는 표현
CONCERN_WORDS = {
    "acne": ["여드름", "트러블", "뾰루지", "화농성 여드름"],
    "milia": ["좁쌀", "좁쌀 여드름", "오돌토돌한 거"],
}
GENERIC_CONCERNS = ["피부 뒤집어진 거", "홍조", "속건조", "피부 예민한 거", "모공"]

# 제품 종류별 사용 표현
USE_VERBS = {
    "cream": ["{p} 바르고 나서", "{p} 듬뿍 발랐더니", "자기 전에 {p} 바르니까"],
    "serum": ["{p} 쓰고 나서", "{p} 몇 방울씩 썼더니", "{p} 꾸준히 바르니까"],
    "toner": ["{p}로 닦아냈더니", "{p} 아침저녁으로 쓰니까", "{p} 바꾸고 나서"],
    "sun": ["{p} 바꾸고 나서", "{p} 매일 발랐더니", "선크림을 {p}로 바꾸니까"],
}
DEFAULT_USES = ["{p} 쓰고 나서", "{p} 알게 되고", "{p} 꾸준히 썼더니"]

OPENERS = [
    "저도 {c} 때문에 고생했는데",
    "{c} 때문에 진짜 힘들었는데",
    "저도 {c} 심했었는데",
    "{c}로 고민 많았는데",
    "몇 년째 {c} 달고 살았는데",
    "피부과 다녀도 {c} 안 잡혔는데",
]
RESULTS = [
    "진짜 좋아졌어요",
    "확실히 가라앉았어요",
    "피부가 많이 편해졌어요",
    "눈에 띄게 줄었어요",
    "2주 만에 달라졌어요",
    "요즘 화장이 잘 먹어요",
]
TAILS = ["", " 강추합니다", " 다들 써보세요", " 인생템이에요", " 고민이신 분들 한번 써보세요", " 재구매 각"]

# 캠페인 계정끼리 서로 다는 맞장구 답글
SUPPORT_REPLIES = [
    "저도 {p} 쓰는데 진짜 좋아요",
    "헐 저도 이거 쓰고 효과 봤어요",
    "{p} 인정이요",
    "저도 써볼게요!",
    "이거 진짜예요 저도 좋아졌어요",
]

# 일반 댓글 (제품 언급 없음 / 중립·부정 언급)
ORGANIC_PLAIN = [
    "영상 잘 봤어요",
    "설명 너무 꼼꼼하시네요",
    "구독하고 갑니다",
    "목소리 좋으세요",
    "다음엔 {c} 관리 영상도 해주세요",
    "저는 {c} 때문에 피부과 다녀요",
    "피부 진짜 좋으시다",
    "편집 너무 깔끔해요",
    "오늘도 잘 보고 가요",
]
ORGANIC_MENTION = [
    "{p} 저도 써봤는데 저는 그냥 그랬어요",
    "{p} 가격이 좀 아쉽네요",
    "{p} 이랑 같이 써도 되나요?",
    "{p} 쓰다가 저는 따가워서 바꿨어요",
    "{p} 요즘 세일하던데",
    "{p} 향이 좀 별로였어요",
    "{p} 무난하게 쓰기 좋아요",
]
# 유명 제품에 대한 자연스러운 칭찬 (구조는 비슷하지만 조직적이지 않은 오탐 함정)
ORGANIC_PRAISE = [
    "{p}는 진짜 스테디셀러죠",
    "{p} 몇 통째 쓰는지 모르겠어요",
    "{c} 있을 때 {p} 바르면 좀 낫더라구요",
    "역시 {p}",
]
ORGANIC_REPLIES = ["저도요", "ㅋㅋㅋ 공감", "오 정보 감사해요", "저는 별로였어요", "그거 어디서 사요?", "맞아요"]

EMOJI = ["😊", "👍", "🥹", "✨", "💯", "ㅠㅠ", "ㅎㅎ", "ㅋㅋ", "!!"]


def concern_word(rng: random.Random, product: Product | None = None) -> str:
    if product and product.concerns and rng.random() < 0.8:
        code = rng.choice(product.concerns)
        if code in CONCERN_WORDS:
            return rng.choice(CONCERN_WORDS[code])
    pool = [w for ws in CONCERN_WORDS.values() for w in ws] + GENERIC_CONCERNS
    return rng.choice(pool)


def add_noise(rng: random.Random, text: str, strength: float) -> str:
    """띄어쓰기 빠뜨림, 이모지, 반복 문자 같은 실제 댓글 잡음을 넣는다."""
    if rng.random() < strength * 0.4:
        words = text.split(" ")
        if len(words) > 2:
            i = rng.randrange(len(words) - 1)
            words[i : i + 2] = [words[i] + words[i + 1]]
            text = " ".join(words)
    if rng.random() < strength * 0.5:
        text += " " + rng.choice(EMOJI)
    if rng.random() < strength * 0.2:
        text = text.replace("요", "용", 1)
    return text


def campaign_text(rng: random.Random, product: Product, variation: float) -> str:
    """같은 구조, 다른 표현. variation이 클수록 고르는 재료 범위가 넓어진다."""

    def pick(pool: list[str]) -> str:
        k = max(1, round(len(pool) * variation))
        return rng.choice(pool[:k])

    name = product.mention(rng, variation)
    opener = pick(OPENERS).format(c=concern_word(rng, product))
    use = pick(USE_VERBS.get(product.category, DEFAULT_USES)).format(p=name)
    result = pick(RESULTS)
    tail = pick(TAILS) if rng.random() < 0.3 + variation / 2 else ""
    return add_noise(rng, f"{opener} {use} {result}{tail}", strength=variation)


def support_reply(rng: random.Random, product: Product, variation: float) -> str:
    return add_noise(rng, rng.choice(SUPPORT_REPLIES).format(p=product.mention(rng, variation)), variation)


def organic_text(rng: random.Random, product: Product | None) -> str:
    if product is None:
        text = rng.choice(ORGANIC_PLAIN).format(c=concern_word(rng))
    else:
        text = rng.choice(ORGANIC_MENTION).format(p=product.mention(rng, 0.6))
    return add_noise(rng, text, strength=0.6)


def praise_text(rng: random.Random, product: Product) -> str:
    return add_noise(rng, rng.choice(ORGANIC_PRAISE).format(p=product.mention(rng, 0.6), c=concern_word(rng, product)), 0.6)


def organic_reply(rng: random.Random) -> str:
    return add_noise(rng, rng.choice(ORGANIC_REPLIES), strength=0.6)

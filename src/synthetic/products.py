"""제품 사전(resources/product_dict.json)을 생성기에서 쓰는 형태로 읽는다."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

# 일반 댓글에서 제품이 언급될 상대 비율. 유명할수록 자연스럽게 많이 언급된다.
TIER_WEIGHT = {"유명": 5.0, "중간": 1.0, "저언급": 0.2}


@dataclass(frozen=True)
class Alias:
    text: str
    needs_brand: bool


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    name_needs_brand: bool
    brand: str
    brand_aliases: tuple[str, ...]
    aliases: tuple[Alias, ...]
    category: str
    tier: str
    concerns: tuple[str, ...]

    @property
    def weight(self) -> float:
        return TIER_WEIGHT.get(self.tier, 1.0)

    def mention(self, rng: random.Random, variation: float = 0.5) -> str:
        """댓글에 들어갈 제품 표기 하나를 고른다.

        variation이 클수록 별칭·브랜드 별칭을 더 자주 쓴다.
        브랜드 없이는 무슨 제품인지 모르는 표기(needs_brand)는 브랜드를 붙인다.
        """
        forms = [Alias(self.name, self.name_needs_brand)]
        if self.aliases and rng.random() < variation:
            forms = list(self.aliases)
        form = rng.choice(forms)
        if form.needs_brand or rng.random() < 0.3:
            brand = rng.choice((self.brand, *self.brand_aliases)) if rng.random() < variation else self.brand
            return f"{brand} {form.text}"
        return form.text


def load_products(path: str | Path) -> list[Product]:
    """status가 active인 제품만 읽는다."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    products = []
    for p in raw:
        if p.get("status", "active") != "active":
            continue
        products.append(
            Product(
                product_id=p["product_id"],
                name=p["product_name"],
                name_needs_brand=bool(p.get("name_needs_brand")),
                brand=p["brand"],
                brand_aliases=tuple(a["text"] for a in p.get("brand_aliases") or []),
                aliases=tuple(Alias(a["text"], bool(a.get("needs_brand"))) for a in p.get("aliases") or []),
                category=p.get("category") or "etc",
                tier=p.get("awareness_tier") or "중간",
                concerns=tuple(p.get("concerns") or []),
            )
        )
    if not products:
        raise ValueError(f"active 제품이 없습니다: {path}")
    return products

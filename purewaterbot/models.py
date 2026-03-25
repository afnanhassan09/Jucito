from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


Role = Literal["user", "assistant", "system"]


@dataclass(frozen=True)
class MenuItem:
    """
    Canonical in-app representation of a product.
    Prices are stored as integer cents to avoid float issues.
    """

    id: str
    name: str
    description: str
    tags: list[str]
    price_cents: int
    category: str = "General"
    available: bool = True

    def format_price(self, currency: str = "USD") -> str:
        dollars = self.price_cents / 100.0
        if currency.upper() == "USD":
            return f"${dollars:,.2f}"
        return f"{dollars:,.2f} {currency.upper()}"


@dataclass
class CartItem:
    menu_item_id: str
    quantity: int = 1
    notes: str = ""
    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "menu_item_id": self.menu_item_id,
            "quantity": self.quantity,
            "notes": self.notes,
            "added_at": self.added_at.isoformat(),
        }


@dataclass
class ChatMessage:
    role: Role
    content: str
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content, "ts": self.ts.isoformat()}


@dataclass
class Session:
    """
    Per-user state.
    - cart: list of CartItem
    - address: str | None
    - history: list of ChatMessage (for future LLM/context use)
    """

    user_id: str
    cart: list[CartItem] = field(default_factory=list)
    address: str | None = None
    history: list[ChatMessage] = field(default_factory=list)
    awaiting_confirmation: bool = False


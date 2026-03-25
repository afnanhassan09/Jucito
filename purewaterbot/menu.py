from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .models import MenuItem


@dataclass(frozen=True)
class Menu:
    currency: str
    items_by_id: dict[str, MenuItem]

    def list_items(self) -> list[MenuItem]:
        return list(self.items_by_id.values())

    def get(self, item_id: str) -> MenuItem | None:
        return self.items_by_id.get(item_id)


def _price_to_cents(price: object) -> int:
    # Accept numeric (int/float) or numeric strings; store as integer cents.
    if isinstance(price, int):
        return price * 100
    if isinstance(price, float):
        return int(round(price * 100))
    if isinstance(price, str):
        return int(round(float(price) * 100))
    raise TypeError(f"Unsupported price type: {type(price)}")


from purewaterbot.db import get_menu_collection

def load_menu(menu_path: str | Path = None) -> Menu:
    collection = get_menu_collection()
    raw = list(collection.find({}))
    
    currency = "PKR" # Default for now
    
    items_by_id: dict[str, MenuItem] = {}
    for it in raw:
        category = str(it.get("category", "General"))
        available = bool(it.get("available", True))

        item = MenuItem(
            id=str(it["id"]),
            name=str(it["name"]),
            description=str(it.get("description", "")),
            tags=[str(t) for t in it.get("tags", [])],
            price_cents=_price_to_cents(it.get("price", 0)),
            category=category,
            available=available,
        )
        items_by_id[item.id] = item
        
    # If the database is empty, it might be good to seed it if necessary, but we can do that via the frontend.
    return Menu(currency=currency, items_by_id=items_by_id)


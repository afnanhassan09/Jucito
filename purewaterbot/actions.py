from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from purewaterbot.menu import load_menu
from purewaterbot.models import CartItem
from purewaterbot.session import SessionManager


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def _find_item_id_by_name_or_id(menu, item_name: str) -> str | None:
    if not item_name:
        return None
    # Exact ID match first (case-sensitive then case-insensitive).
    if item_name in menu.items_by_id:
        return item_name
    lowered = item_name.strip().lower()
    for item_id in menu.items_by_id.keys():
        if item_id.lower() == lowered:
            return item_id

    # Name match (normalized, case-insensitive).
    target = _norm(item_name)
    for it in menu.items_by_id.values():
        if _norm(it.name) == target:
            return it.id
    return None


def add_to_cart(menu, sessions: SessionManager, user_id: str, item_name: str, quantity: int, notes: str) -> dict:
    item_id = _find_item_id_by_name_or_id(menu, item_name)
    if not item_id:
        return {"success": False, "message": f"Item not found on menu: {item_name}"}

    if not isinstance(quantity, int):
        try:
            quantity = int(quantity)
        except Exception:
            return {"success": False, "message": "Quantity must be a whole number."}
    if quantity <= 0:
        return {"success": False, "message": "Quantity must be >= 1."}

    notes = str(notes or "").strip()
    s = sessions.get_or_create(user_id)
    menu_item = menu.get(item_id)

    if not menu_item.available:
        return {"success": False, "message": f"Item '{menu_item.name}' is currently out of stock."}

    # Merge line items by (menu_item_id + notes) so customizations don’t collide.
    for ci in s.cart:
        if ci.menu_item_id == item_id and (ci.notes or "") == notes:
            ci.quantity += quantity
            it = menu.get(item_id)
            return {
                "success": True,
                "message": f"Success: Added {quantity} × {it.name}. Now {ci.quantity} in cart.",
            }

    s.cart.append(CartItem(menu_item_id=item_id, quantity=quantity, notes=notes))
    it = menu.get(item_id)
    return {"success": True, "message": f"Success: Added {quantity} × {it.name} to cart."}


def remove_from_cart(menu, sessions: SessionManager, user_id: str, item_name: str) -> dict:
    item_id = _find_item_id_by_name_or_id(menu, item_name)
    if not item_id:
        return {"success": False, "message": f"Item not found on menu: {item_name}"}

    s = sessions.get_or_create(user_id)
    before = len(s.cart)
    s.cart = [ci for ci in s.cart if ci.menu_item_id != item_id]
    removed = before - len(s.cart)
    it = menu.get(item_id)
    if removed <= 0:
        return {"success": False, "message": f"{it.name} is not in the cart."}
    return {"success": True, "message": f"Success: Removed {it.name} from cart."}


def get_cart_details(menu, sessions: SessionManager, user_id: str) -> dict:
    s = sessions.get_or_create(user_id)
    items = []
    total_cents = 0
    for ci in s.cart:
        it = menu.get(ci.menu_item_id)
        if not it:
            continue
        line_total = it.price_cents * ci.quantity
        total_cents += line_total
        items.append(
            {
                "id": it.id,
                "name": it.name,
                "quantity": ci.quantity,
                "notes": ci.notes,
                "unit_price_cents": it.price_cents,
                "line_total_cents": line_total,
            }
        )

    return {
        "success": True,
        "currency": menu.currency,
        "address": s.address,
        "items": items,
        "total_cents": total_cents,
        "total_formatted": f"${total_cents/100.0:,.2f}" if menu.currency.upper() == "USD" else f"{total_cents/100.0:,.2f} {menu.currency}",
    }


def set_shipping_address(menu, sessions: SessionManager, user_id: str, address: str) -> dict:
    addr = str(address or "").strip()
    if not addr:
        return {"success": False, "message": "Address cannot be empty."}
    s = sessions.get_or_create(user_id)
    s.address = addr
    return {"success": True, "message": f"Success: Shipping address set to: {addr}"}


def finalize_order(menu, sessions: SessionManager, user_id: str, confirmation: bool, orders_path: Path) -> dict:
    """
    Strict gatekeeper:
    - cart must not be empty
    - address must be present
    - confirmation must be True to place order
    On success: append to data/orders.json and clear session cart+address.
    """
    s = sessions.get_or_create(user_id)

    if len(s.cart) == 0:
        return {"success": False, "message": "ERROR: Cart is empty. Ask user to add items."}

    addr = (s.address or "").strip()
    if not addr:
        return {"success": False, "message": "ERROR: Address is missing. Ask user for address."}

    if not bool(confirmation):
        return {"success": True, "status": "pending", "message": "Order cancelled/pending."}

    # Build order payload
    cart_details = get_cart_details(menu, sessions, user_id=user_id)
    order_id = uuid.uuid4().hex[:10].upper()
    now = datetime.now(timezone.utc).isoformat()
    order = {
        "id": order_id,
        "user_id": user_id,
        "created_at": now,
        "currency": cart_details.get("currency", menu.currency),
        "address": addr,
        "items": cart_details.get("items", []),
        "total_cents": cart_details.get("total_cents", 0),
        "total_formatted": cart_details.get("total_formatted", ""),
        "status": "pending_payment",
        "payment_screenshot": None
    }

    # Write to MongoDB
    from purewaterbot.db import get_orders_collection
    orders_collection = get_orders_collection()
    orders_collection.insert_one(order)
    print(f"[finalize_order] ORDER SAVED to MongoDB: id={order_id}, user={user_id}, status=pending_payment", flush=True)
    
    # We remove '_id' from the order object to be safe although the response doesn't strictly return the whole object
    if '_id' in order:
        del order['_id']

    # Clear session cart + address for next order.
    s.cart = []
    s.address = None

    return {
        "success": True,
        "status": "placed",
        "order_id": order_id,
        "message": f"SUCCESS: Order #{order_id} placed successfully. Thank you!",
    }

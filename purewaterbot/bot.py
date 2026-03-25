from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import uuid
from pathlib import Path

from .menu import Menu, load_menu
from .models import CartItem
from .session import SessionManager


@dataclass(frozen=True)
class BotReply:
    text: str


class BotEngine:
    """
    Phase 1 bot engine: purely deterministic command handling.
    Later phases can swap this for LLM-driven intent parsing.
    """

    def __init__(self, menu: Menu, sessions: SessionManager, repo_root: Path | None = None) -> None:
        self.menu = menu
        self.sessions = sessions
        self.repo_root = repo_root

    @classmethod
    def from_repo_root(cls, repo_root: str | Path) -> "BotEngine":
        root = Path(repo_root)
        menu = load_menu(root / "data" / "menu.json")
        sessions = SessionManager()
        return cls(menu=menu, sessions=sessions, repo_root=root)

    def handle_message(self, user_id: str, text: str) -> BotReply:
        text = (text or "").strip()
        self.sessions.append_history(user_id, "user", text)

        if not text:
            return self._reply(user_id, "Say `menu` to view products, or `help` for commands.")

        s = self.sessions.get_or_create(user_id)
        lower = text.lower()

        # Handle confirmation state
        if s.awaiting_confirmation:
            if lower in {"yes", "y", "confirm", "ok"}:
                return self._finalize_order(user_id)
            elif lower in {"no", "n", "cancel"}:
                s.awaiting_confirmation = False
                return self._reply(user_id, "Order cancelled. You can continue shopping.")
            else:
                return self._reply(
                    user_id, "Please type `yes` to confirm your order, or `no` to cancel."
                )

        if lower in {"help", "h", "?"}:
            return self._reply(
                user_id,
                "\n".join(
                    [
                        "Commands:",
                        "- menu                     (list products)",
                        "- add <ITEM_ID> [qty]      (add to cart)",
                        "- cart                     (view cart)",
                        "- address <text>           (set delivery address)",
                        "- checkout                 (show order summary)",
                        "- reset                    (clear session)",
                        "- session                  (debug: dump session)",
                        "- quit                     (exit CLI)",
                    ]
                ),
            )

        if lower == "menu":
            lines = ["Menu:"]
            for item in self.menu.list_items():
                lines.append(
                    f"- {item.id}: {item.name} — {item.format_price(self.menu.currency)}"
                )
            lines.append("")
            lines.append("Tip: `add WATER_1L 2`")
            return self._reply(user_id, "\n".join(lines))

        if lower.startswith("add "):
            parts = text.split()
            if len(parts) < 2:
                return self._reply(user_id, "Usage: add <ITEM_ID> [qty]")
            item_id = parts[1].strip()
            qty = 1
            if len(parts) >= 3:
                try:
                    qty = int(parts[2])
                except ValueError:
                    return self._reply(user_id, "Quantity must be a whole number.")
            if qty <= 0:
                return self._reply(user_id, "Quantity must be >= 1.")

            item = self.menu.get(item_id)
            if not item:
                return self._reply(user_id, f"Unknown item id: {item_id}. Type `menu`.")

            s = self.sessions.get_or_create(user_id)
            # If already in cart, increment.
            for ci in s.cart:
                if ci.menu_item_id == item_id:
                    ci.quantity += qty
                    return self._reply(
                        user_id, f"Added {qty} × {item.name}. Now {ci.quantity} in cart."
                    )
            s.cart.append(CartItem(menu_item_id=item_id, quantity=qty))
            return self._reply(user_id, f"Added {qty} × {item.name} to cart.")

        if lower == "cart":
            return self._reply(user_id, self._format_cart(user_id))

        if lower.startswith("address "):
            addr = text[len("address ") :].strip()
            if not addr:
                return self._reply(user_id, "Usage: address <text>")
            s = self.sessions.get_or_create(user_id)
            s.address = addr
            return self._reply(user_id, f"Address set to: {addr}")

        if lower == "checkout":
            s = self.sessions.get_or_create(user_id)
            if not s.cart:
                return self._reply(user_id, "Your cart is empty. Type `menu` to browse.")
            if not s.address:
                return self._reply(
                    user_id, "Please set your address first: `address 123 Main St`"
                )
            total_cents = self._cart_total_cents(user_id)
            total = f"${total_cents/100.0:,.2f}"
            reply_text = "\n".join(
                    [
                        "Order summary:",
                        self._format_cart(user_id),
                        f"Deliver to: {s.address}",
                        f"Total: {total}",
                        "",
                        f"Total: {total}",
                        "",
                        "Ready to order? Type `yes` to confirm.",
                    ]
                )
            
            # flag for confirmation
            s.awaiting_confirmation = True
            return self._reply(user_id, reply_text)

        if lower == "reset":
            self.sessions.reset(user_id)
            return self._reply(user_id, "Session reset. Type `menu` to start over.")

        if lower == "session":
            data = self.sessions.export_session(user_id)
            # Pretty-ish, but keep it readable without importing json.
            return self._reply(user_id, f"Session:\n{data}")

        return self._reply(
            user_id,
            "I didn't understand. Type `help` for commands, or `menu` to browse.",
        )

    def _reply(self, user_id: str, text: str) -> BotReply:
        self.sessions.append_history(user_id, "assistant", text)
        return BotReply(text=text)

    def _cart_total_cents(self, user_id: str) -> int:
        s = self.sessions.get_or_create(user_id)
        total = 0
        for ci in s.cart:
            item = self.menu.get(ci.menu_item_id)
            if item:
                total += item.price_cents * ci.quantity
        return total

    def _format_cart(self, user_id: str) -> str:
        s = self.sessions.get_or_create(user_id)
        if not s.cart:
            return "Cart is empty."

        lines = ["Cart:"]
        for ci in s.cart:
            item = self.menu.get(ci.menu_item_id)
            if not item:
                lines.append(f"- {ci.menu_item_id}: {ci.quantity} (missing from menu)")
                continue
            lines.append(
                f"- {item.name} ({item.id}) × {ci.quantity} — {item.format_price(self.menu.currency)} each"
            )
        total_cents = self._cart_total_cents(user_id)
        lines.append(f"Total: ${total_cents/100.0:,.2f}")
        return "\n".join(lines)

    def _finalize_order(self, user_id: str) -> BotReply:
        s = self.sessions.get_or_create(user_id)
        if not s.cart:
            s.awaiting_confirmation = False
            return self._reply(user_id, "Cart is empty. Order cancelled.")

        # Logic from bot_server.py
        cart_summary = self._format_cart(user_id)
        total_cents = self._cart_total_cents(user_id)
        
        order_id = uuid.uuid4().hex[:8].upper()
        now = datetime.now(timezone.utc).isoformat()
        
        # Build strict order object
        items = []
        for ci in s.cart:
            item = self.menu.get(ci.menu_item_id)
            if item:
                items.append({
                    "id": item.id,
                    "name": item.name,
                    "quantity": ci.quantity,
                    "unit_price_cents": item.price_cents,
                    "line_total_cents": item.price_cents * ci.quantity
                })

        order = {
            "id": order_id,
            "user_id": user_id,
            "created_at": now,
            "currency": self.menu.currency,
            "address": s.address,
            "items": items,
            "total_cents": total_cents,
            "total_formatted": f"${total_cents/100.0:,.2f}"
        }

        # Persist if we know where
        if self.repo_root:
            orders_path = self.repo_root / "data" / "orders.json"
            orders_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if orders_path.exists():
                    data = json.loads(orders_path.read_text(encoding="utf-8") or "[]")
                    if not isinstance(data, list):
                        data = []
                else:
                    data = []
                data.append(order)
                orders_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except Exception as e:
                return self._reply(user_id, f"Error saving order: {e}")

        # Clear session
        s.cart = []
        s.address = None
        s.awaiting_confirmation = False

        return self._reply(
            user_id, 
            f"Order #{order_id} confirmed! We will ship to {order['address']}.\nThank you for your business."
        )


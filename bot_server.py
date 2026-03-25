from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from purewaterbot.bot import BotEngine
from purewaterbot.menu import load_menu
from purewaterbot.session import SessionManager
from purewaterbot.actions import (
    add_to_cart,
    remove_from_cart,
    get_cart_details,
    set_shipping_address,
    finalize_order,
)





def main() -> int:
    repo_root = Path(__file__).resolve().parent
    # Keep the Phase 1 engine for backwards compatibility (text commands),
    # but Phase 2 uses LLM tool-calls executed against the same in-memory sessions.
    engine = BotEngine.from_repo_root(repo_root)
    menu = load_menu() # Data dynamically fetched from MongoDB
    sessions = engine.sessions  # reuse the same SessionManager instance

    # Protocol: one JSON object per line in, one JSON object per line out.
    # Input:  {"user_id":"u1","text":"menu"}
    # Output: {"ok":true,"reply":"..."}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError("Expected a JSON object like {\"user_id\":\"u1\",\"text\":\"...\"}")
            user_id = str(msg.get("user_id", "test-user"))
            msg_type = str(msg.get("type", "")).strip().lower()

            if msg_type == "history":
                role = str(msg.get("role", "user"))
                content = str(msg.get("content", ""))
                sessions.append_history(user_id=user_id, role=role, content=content)
                sys.stdout.write(json.dumps({"ok": True, "result": {"success": True}}) + "\n")
                sys.stdout.flush()
                continue

            if msg_type == "tool":
                name = str(msg.get("name", ""))
                arguments = msg.get("arguments", {})
                if not isinstance(arguments, dict):
                    raise ValueError("tool.arguments must be an object")

                if name == "add_to_cart":
                    result = add_to_cart(
                        menu,
                        sessions,
                        user_id=user_id,
                        item_name=str(arguments.get("item_name", "")),
                        quantity=arguments.get("quantity", 1),
                        notes=str(arguments.get("notes", "")),
                    )
                elif name == "remove_from_cart":
                    result = remove_from_cart(
                        menu,
                        sessions,
                        user_id=user_id,
                        item_name=str(arguments.get("item_name", "")),
                    )
                elif name == "get_cart_details":
                    result = get_cart_details(menu, sessions, user_id=user_id)
                elif name == "set_shipping_address":
                    result = set_shipping_address(
                        menu,
                        sessions,
                        user_id=user_id,
                        address=str(arguments.get("address", "")),
                    )
                elif name == "finalize_order":
                    result = finalize_order(
                        menu,
                        sessions,
                        user_id=user_id,
                        confirmation=bool(arguments.get("confirmation", False)),
                        orders_path=repo_root / "data" / "orders.json",
                    )
                else:
                    result = {"success": False, "message": f"Unknown tool: {name}"}

                sys.stdout.write(json.dumps({"ok": True, "result": result}) + "\n")
                sys.stdout.flush()
                continue

            # Default: Phase 1 text handling
            text = str(msg.get("text", ""))
            reply = engine.handle_message(user_id=user_id, text=text)
            sys.stdout.write(json.dumps({"ok": True, "reply": reply.text}) + "\n")
            sys.stdout.flush()
        except Exception as e:  # noqa: BLE001 (keep robust for phase 1)
            sys.stdout.write(json.dumps({"ok": False, "error": str(e)}) + "\n")
            sys.stdout.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


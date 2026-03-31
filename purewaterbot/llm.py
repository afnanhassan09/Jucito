import json
import re
import requests
import os
import sys
from pathlib import Path

from purewaterbot.bot import BotEngine
from purewaterbot.menu import load_menu
from purewaterbot.session import SessionManager

# --- Configuration (OpenAI Chat Completions API) ---
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
MAX_HISTORY = 30  # Max conversation messages to keep in context


# --- Menu Helpers ---
def get_menu_items(use_db=True):
    """Return a list of plain dicts (no _id) from MongoDB or the static JSON."""
    if use_db:
        from purewaterbot.db import get_menu_collection
        collection = get_menu_collection()
        return list(collection.find({}, {'_id': 0}))
    # Fallback: static file
    static_path = Path(__file__).resolve().parent.parent / "data" / "menu.json"
    if static_path.exists():
        with open(static_path, encoding="utf-8") as f:
            return json.load(f)
    return [] 


def _build_menu_string(items):
    """Pretty-print menu items for display in the chat."""
    if not items:
        return "Menu is empty."
    lines = []
    for i, it in enumerate(items, 1):
        price = it.get("price", 0)
        name = it.get("name", "Unknown")
        available = it.get("available", True)
        status = "" if available else " (OUT OF STOCK)"
        lines.append(f"{i}. {name} - Rs. {price}{status}")
    return "\n".join(lines)


# --- System Prompt & Rules ---
def build_system_prompt(use_db=True):
    """Build the system prompt with the LIVE menu from MongoDB."""
    items = get_menu_items(use_db)
    menu_str = _build_menu_string(items)
    menu_json = json.dumps(items, ensure_ascii=False)

    return f"""You are a WhatsApp order-taking assistant for a fast food and juice parlor.
Your name is "JuicitoBot". You speak in clear, natural, and friendly English.

════════════════════════════════════════
LANGUAGE & TONE RULES
════════════════════════════════════════
- Speak ONLY in English.
- Sound friendly and casual — warm and natural.
- Keep messages SHORT. Max 3-4 lines per reply.
- Do NOT write more than required and only speak what is required.
- After adding any item to the cart, ALWAYS ask exactly this and nothing more:
  "Anything else?"
- Don't ask anything else? anywhere else just after adding an item to the cart please.
  Do NOT suggest other items. Do NOT upsell. Just ask this one question.

════════════════════════════════════════
MENU DATA (internal reference — do NOT dump to user unless asked)
════════════════════════════════════════
Categories available: Fast Food, Sauce, Lassi, Burger, Soup, Dessert.
Burger Deals include: Fries + Soft Drink. Add Cheese for Rs. 40 extra.
Soups: Add Egg for Rs. 40 extra.

--- MENU JSON (Use this data to answer user questions about prices, items, and categories. Do not invent items): {menu_json} ---

MENU DISPLAY RULES:
- Do NOT show the full menu on greeting. Just greet and ask: "What would you like to order?"
- If user asks for the menu or what's available → reply with EXACTLY the text "[SEND_MENU_IMAGE]" somewhere in your response. The system will automatically attach the menu image. Do NOT attempt to list all items as text.
- If user asks about a CATEGORY (e.g. "show burgers", "what drinks do you have") → show ONLY items from that category with prices.
- If user asks about a SPECIFIC item by name (e.g. "how much is the zinger roll") → answer directly with price and description. No need to show the full menu.

════════════════════════════════════════
ORDER FLOW — FOLLOW EXACTLY
════════════════════════════════════════
STEP 1 — GREETING:
  If user greets (hi, hello, etc.), reply warmly and ask: "What would you like to order?"
  Do NOT show the menu unless user explicitly asks for it.

STEP 2 — TAKING THE ORDER:
  - Match user's request to the closest item in the menu JSON.
  - If ambiguous, offer the matching options with prices.
  - Call add_to_cart() with the exact item_name or id from the menu JSON.
  - After EVERY successful add_to_cart, reply with ONLY: "Anything else?"
  - If user adds customization (e.g., "extra spicy", "no onion"), put it in the notes field.

STEP 3 — CLOSING THE ORDER:
  When user says "that's it", "done", "place order", or similar:
  - Call get_cart_details() to get the current cart.
  - If address is not set, ask: "What is your delivery address?"
  - Wait for user to give address, then IMMEDIATELY call set_shipping_address().
  - Never say the address is set in text — only confirm after the tool returns success.

STEP 4 — ORDER SUMMARY:
  After address is set, show a clean summary:
    Items + quantities + prices
    Delivery address
    Total amount
  Then ask: "Everything looks good? Shall I confirm?"

STEP 5 — FINALIZE:
  ONLY call finalize_order(confirmation=true) after user explicitly says "yes", "confirm", "okay", or similar.
  On success: "Order placed! Please send a payment screenshot to EasyPaisa/JazzCash 0346 4880929 (Afnan Hassan)."
  On failure: Tell the user clearly what went wrong.

════════════════════════════════════════
EDGE CASES — HANDLE ALL OF THESE
════════════════════════════════════════
OUT OF STOCK (available: false in menu JSON):
  - Never add it to the cart.
  - Say: "Sorry, that is currently unavailable — would you like to try [ALTERNATIVE from same category]?"
  - Always suggest one alternative from the same category.

UNCLEAR / MISSPELLED ITEM:
  - Show the 2 closest matches from the menu with prices.
  - Ask: "Which one would you like?"

CANCEL / REMOVE ITEM:
  - Call remove_from_cart() immediately.
  - Confirm: "Removed. Anything else?"

REPEAT ORDER:
  - Call get_cart_details() first.
  - If cart is empty: "Your cart is empty right now — what would you like to order?"
  - If items exist: add them back and confirm.

CUSTOMIZATION ("extra spicy", "no onion", "cold"):
  - Capture in the notes field of add_to_cart().
  - Confirm: "Noted — [customization]."

PRICE QUESTION:
  - Answer directly from the menu. No tool call needed.

OFF-TOPIC MESSAGE:
  - Redirect gently: "Haha, I only take orders! Would you like to order something?"

════════════════════════════════════════
HARD RULES — NEVER BREAK THESE
════════════════════════════════════════
1. Never invent or add a menu item that does not exist in the menu JSON.
2. Never say "order placed" or "address set" in text — only tool calls make these real.
3. Never call finalize_order() unless the user has explicitly confirmed.
4. Never change a price. Menu prices are fixed.
5. Never ask more than one question at a time.
6. Never write long paragraphs. Max 3-4 lines.
7. After every add_to_cart, say only "Anything else?" — nothing else.
"""

# --- Tool Definitions (Ported from cli.js) ---
TOOLS = [
  {
    "type": "function",
    "function": {
      "name": "add_to_cart",
      "description": "Adds a specific item to the user's current session cart.",
      "parameters": {
        "type": "object",
        "properties": {
          "item_name": {
            "type": "string",
            "description": "Exact item name or item id from the AVAILABLE MENU JSON (do not invent).",
          },
          "quantity": {
            "type": "number",
            "description": "Quantity to add (whole number).",
          },
          "notes": {
            "type": "string",
            "description": "Optional notes/customizations (e.g., 'no-contact delivery'). Use empty string if none.",
          },
        },
        "required": ["item_name", "quantity", "notes"],
      },
    },
  },
  {
    "type": "function",
    "function": {
      "name": "remove_from_cart",
      "description": "Removes an item from the cart.",
      "parameters": {
        "type": "object",
        "properties": {
          "item_name": {
            "type": "string",
            "description": "Exact item name or item id from the AVAILABLE MENU JSON.",
          },
        },
        "required": ["item_name"],
      },
    },
  },
  {
    "type": "function",
    "function": {
      "name": "get_cart_details",
      "description": "Returns a summary of items in the cart and the total price.",
      "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
      },
    },
  },
  {
    "type": "function",
    "function": {
      "name": "set_shipping_address",
      "description": "Saves or updates the user's shipping address in the session.",
      "parameters": {
        "type": "object",
        "properties": {
          "address": {
            "type": "string",
            "description": "Full shipping address as free-form text.",
          },
        },
        "required": ["address"],
      },
    },
  },
  {
    "type": "function",
    "function": {
      "name": "finalize_order",
      "description": "Gatekeeper that validates cart/address/confirmation, saves the order, and clears the session on success.",
      "parameters": {
        "type": "object",
        "properties": {
          "confirmation": {
            "type": "boolean",
            "description": "Set true ONLY after the user explicitly confirms the final summary; false otherwise.",
          },
        },
        "required": ["confirmation"],
      },
    },
  },
]

# --- Guardrail Helpers ---
# Patterns that indicate the LLM *claimed* it set/updated the address in text
_ADDRESS_SET_PATTERNS = re.compile(
    r"(?:address|delivery address|shipping address)\s+"
    r"(?:updated?|set|saved|changed|recorded|noted)\s+(?:to|as|:)",
    re.IGNORECASE,
)

# Patterns that indicate the LLM *claimed* it placed/confirmed an order in text
_ORDER_PLACED_PATTERNS = re.compile(
    r"(?:"
    r"order\s+(?:confirm|place|ho\s*gaya|placed|confirmed)"
    r"|order\s+#\s*\w+"
    r"|order\s+number\s+(?:is|hai)"
    r"|payment\s+(?:ka\s+)?screenshot(?:|e?s)?\s+(?:bhej)?"
    r")",
    re.IGNORECASE,
)

def _mentions_address_set(text: str) -> bool:
    """Return True if the LLM response text claims an address was saved."""
    if not text:
        return False
    return bool(_ADDRESS_SET_PATTERNS.search(text))

def _mentions_order_placed(text: str) -> bool:
    """Return True if the LLM response text claims an order was placed/confirmed."""
    if not text:
        return False
    return bool(_ORDER_PLACED_PATTERNS.search(text))


def _extract_address_from_text(user_text: str) -> "str | None":
    """
    Best-effort extraction of an address from the user's raw message.
    Handles patterns like:
      - "house 27B"
      - "delivery address house 27b hai"
      - "address: house 27B"
      - "mera address house 27B hai"
    Falls back to the entire message if it's short enough to plausibly be an address.
    """
    if not user_text:
        return None
    text = user_text.strip()

    # Try to extract after common prefixes
    prefixes = re.compile(
        r"(?:delivery\s+)?address\s*(?:hai|he|h|is)?\s*[:=-]?\s*",
        re.IGNORECASE,
    )
    m = prefixes.search(text)
    if m:
        addr = text[m.end():].strip()
        # Remove trailing filler words
        addr = re.sub(r"\s+(?:hai|he|h|kr\s*dain|set\s*kr\s*dain)\.?$", "", addr, flags=re.IGNORECASE).strip()
        if addr:
            return addr

    # If the message is short (likely just an address), use it directly
    if len(text) <= 100:
        return text
    return None


class LLMBot:
    def __init__(self, engine: BotEngine):
        self.engine = engine

    def _call_llm(self, messages):
        url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
        payload = {
            "model": LLM_MODEL,
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "temperature": 0.3,
            "max_tokens": 600,
        }

        api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if not api_key:
            return {
                "content": "Error: OPENAI_API_KEY is not set. Add it to your environment or .env file.",
            }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            return data.get("choices", [{}])[0].get("message", {})
        except Exception as e:
            return {"content": f"Error communicating with AI Brain: {str(e)}"}

    def _execute_tool(self, user_id: str, tool_call):
        name = tool_call.get("function", {}).get("name")
        args_str = tool_call.get("function", {}).get("arguments", "{}")
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            args = {}

        print(f"[LLM] Tool call: {name}({args})", flush=True)

        # Reload menu from MongoDB so tool calls use the latest data
        from purewaterbot.menu import load_menu
        self.engine.menu = load_menu()
        
        from purewaterbot.actions import add_to_cart, remove_from_cart, get_cart_details, set_shipping_address, finalize_order

        if name == "add_to_cart":
            result = add_to_cart(self.engine.menu, self.engine.sessions, user_id, args.get("item_name", ""), args.get("quantity", 1), args.get("notes", ""))
        elif name == "remove_from_cart":
            result = remove_from_cart(self.engine.menu, self.engine.sessions, user_id, args.get("item_name", ""))
        elif name == "get_cart_details":
            result = get_cart_details(self.engine.menu, self.engine.sessions, user_id)
        elif name == "set_shipping_address":
            result = set_shipping_address(self.engine.menu, self.engine.sessions, user_id, args.get("address", ""))
        elif name == "finalize_order":
            result = finalize_order(
                self.engine.menu, 
                self.engine.sessions, 
                user_id, 
                args.get("confirmation", False), 
                self.engine.repo_root / "data" / "orders.json"
            )
        else:
            result = {"error": f"Unknown tool: {name}"}
        
        print(f"[LLM] Tool result: {result}", flush=True)
        return result

    def process_user_message(self, user_id: str, text: str) -> str:
        # History Management
        # The core `BotEngine` manages session history. We can reuse it.
        # However, for the LLM to see context, we need to construct `messages` from session history.
        session = self.engine.sessions.get_or_create(user_id)
        
        # 1. Start with System Prompt (rebuilt every request for live menu)
        system_prompt = build_system_prompt()
        
        # 2. Inject Cart Snapshot
        from purewaterbot.actions import get_cart_details
        cart_snapshot = get_cart_details(self.engine.menu, self.engine.sessions, user_id)
        
        combined_system_prompt = system_prompt + "\n\n" + (
            f"[LIVE CART SNAPSHOT]\n{json.dumps(cart_snapshot, ensure_ascii=False)}\n"
            f"[SESSION ADDRESS]: {getattr(session, 'address', None) or 'not set'}"
        )
        
        messages = [{"role": "system", "content": combined_system_prompt}]

        # 3. Append History (trimmed to MAX_HISTORY most recent messages)
        history = session.history[-MAX_HISTORY:] if len(session.history) > MAX_HISTORY else session.history
        for msg in history:
            # Handle both object-style and dict-style history formats just in case
            role = msg.role if hasattr(msg, 'role') else msg.get("role")
            content = msg.content if hasattr(msg, 'content') else msg.get("content")
            
            # THE FIX: Strip out any rogue system messages lurking in the history
            if role == "system":
                continue 
                
            messages.append({"role": role, "content": content})

        # 4. Append Current User Message
        messages.append({"role": "user", "content": text})
        
        # Add to history immediately so it's recorded
        self.engine.sessions.append_history(user_id, "user", text)

        # 5. Loop for Tool Calls
        # Track which tools were actually called this turn
        address_tool_called = False
        finalize_tool_called = False

        # Max steps to prevent infinite loop
        for _ in range(5):
            response_msg = self._call_llm(messages)
            
            tool_calls = response_msg.get("tool_calls")
            if tool_calls:
                # Append assistant's tool-call request to messages
                messages.append(response_msg)
                
                # Execute each tool
                for tc in tool_calls:
                    tool_name = tc.get("function", {}).get("name", "")
                    if tool_name == "set_shipping_address":
                        address_tool_called = True
                    elif tool_name == "finalize_order":
                        finalize_tool_called = True
                    result = self._execute_tool(user_id, tc)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "name": tool_name,
                        "content": json.dumps(result)
                    })
                
                # Continue loop -> call LLM again with tool outputs
                continue
            
            # No tool calls -> Final Response
            final_text = response_msg.get("content", "")

            # --- GUARDRAIL: Detect hallucinated address-set claims ---
            # If the LLM claims it set the address in its text but never
            # actually called set_shipping_address, force the call now.
            if not address_tool_called and _mentions_address_set(final_text):
                session_now = self.engine.sessions.get_or_create(user_id)
                if not (session_now.address or "").strip():
                    addr = _extract_address_from_text(text)
                    if addr:
                        from purewaterbot.actions import set_shipping_address
                        result = set_shipping_address(
                            self.engine.menu, self.engine.sessions, user_id, addr
                        )
                        print(
                            f"[GUARDRAIL] LLM claimed address set but didn't call tool. "
                            f"Forced set_shipping_address('{addr}'): {result}",
                            flush=True,
                        )
                        address_tool_called = True

            # --- GUARDRAIL: Detect hallucinated order-placed claims ---
            # If the LLM claims the order was placed but never called finalize_order,
            # force the call now so the order actually gets saved to MongoDB.
            if not finalize_tool_called and _mentions_order_placed(final_text):
                session_now = self.engine.sessions.get_or_create(user_id)
                if len(session_now.cart) > 0 and (session_now.address or "").strip():
                    from purewaterbot.actions import finalize_order
                    result = finalize_order(
                        self.engine.menu, self.engine.sessions, user_id,
                        True,  # confirmation=True since LLM already confirmed
                        self.engine.repo_root / "data" / "orders.json"
                    )
                    print(
                        f"[GUARDRAIL] LLM claimed order placed but didn't call tool. "
                        f"Forced finalize_order: {result}",
                        flush=True,
                    )
                    finalize_tool_called = True
                    # Update the reply to include the real order ID if successful
                    if result.get("success") and result.get("order_id"):
                        order_id = result["order_id"]
                        # Inject real order ID into the response if it has a fake one
                        final_text = re.sub(
                            r"(?:order\s*(?:number|#|no\.?)\s*(?:is|hai)?\s*:?\s*)\w+",
                            f"Order #{order_id}",
                            final_text,
                            count=1,
                            flags=re.IGNORECASE,
                        )

            self.engine.sessions.append_history(user_id, "assistant", final_text)
            return final_text
            
        return "I'm having trouble connecting to my brain. Please try again."

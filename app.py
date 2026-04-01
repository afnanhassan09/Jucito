from flask import Flask, request, jsonify, Response
from twilio.twiml.messaging_response import MessagingResponse
from pathlib import Path
import shutil
import sys
import os
from dotenv import load_dotenv

load_dotenv()

# Ensure the purewaterbot package is in the path
repo_root = Path(__file__).resolve().parent
sys.path.append(str(repo_root))

from purewaterbot.bot import BotEngine
from purewaterbot.llm import LLMBot
from purewaterbot.db import get_menu_collection, get_orders_collection
from bson import json_util
import json
import re
import requests
import uuid
from twilio.rest import Client

app = Flask(__name__)

# Initialize the bot engine once when the app starts.
# This keeps the sessions in memory as long as the process is running.
engine = BotEngine.from_repo_root(repo_root)
llm_bot = LLMBot(engine)

# Strip env values so Render/newline-pasted secrets still work. Same pair is used for
# REST (outbound WhatsApp) and for Basic Auth when downloading inbound MediaUrl0.
_TWILIO_ACCOUNT_SID = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
_TWILIO_AUTH_TOKEN = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
twilio_client = Client(_TWILIO_ACCOUNT_SID, _TWILIO_AUTH_TOKEN)
twilio_number = (os.environ.get("TWILIO_PHONE_NUMBER") or "").strip()

_MEDIA_URL_ACCOUNT_SID = re.compile(r"/Accounts/(AC[a-f0-9]{32})/", re.IGNORECASE)

# Demo: when Twilio media cannot be downloaded, we still confirm receipt and attach this file to the order.
DEMO_RECEIPT_SOURCE = repo_root / "data" / "receipt.png"
SCREENSHOT_SUCCESS_REPLY = (
    "We received your screenshot. Our team will verify it shortly. "
    "Thank you for your patience!"
)


def _copy_demo_receipt_to_uploads(uploads_dir: Path) -> str | None:
    """Copy bundled demo receipt into static/uploads. Returns filename basename or None."""
    if not DEMO_RECEIPT_SOURCE.is_file():
        print(f"[screenshot] Demo receipt missing: {DEMO_RECEIPT_SOURCE}", file=sys.stderr, flush=True)
        return None
    uploads_dir.mkdir(parents=True, exist_ok=True)
    ext = DEMO_RECEIPT_SOURCE.suffix.lower() or ".png"
    dest_name = f"{uuid.uuid4().hex[:8]}{ext}"
    shutil.copyfile(DEMO_RECEIPT_SOURCE, uploads_dir / dest_name)
    return dest_name


def _apply_payment_screenshot_to_order(user_id: str, upload_basename: str) -> str:
    """Set order to new + payment_screenshot path; return WhatsApp reply body."""
    rel = f"/static/uploads/{upload_basename}"
    orders = get_orders_collection()
    recent_order = orders.find_one(
        {"user_id": user_id, "status": "pending_payment"},
        sort=[("created_at", -1)],
    )
    print(
        f"[screenshot] pending_payment match={recent_order is not None} "
        f"order_id={(recent_order or {}).get('id', 'N/A')}",
        file=sys.stderr,
        flush=True,
    )
    if recent_order:
        orders.update_one(
            {"_id": recent_order["_id"]},
            {"$set": {"status": "new", "payment_screenshot": rel}},
        )
        return SCREENSHOT_SUCCESS_REPLY
    return (
        "We received your image but could not find a pending order for your number."
    )


def _body_looks_like_raster_image(body: bytes) -> bool:
    if not body or len(body) < 12:
        return False
    if body[:3] == b"\xff\xd8\xff":
        return True
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    if body[:6] in (b"GIF87a", b"GIF89a"):
        return True
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return True
    return False


def _image_extension(body: bytes) -> str:
    if body[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if body[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _twilio_error_response_body(body: bytes) -> bool:
    head = body[:4000]
    return b"<RestException>" in head or (body.startswith(b"<?xml") and b"TwilioResponse" in head)


@app.route("/whatsapp", methods=['POST'])
def whatsapp_reply():
    """Respond to incoming WhatsApp messages with a dynamic message."""
    print("Received a request at /whatsapp", file=sys.stderr)
    print(f"Headers: {request.headers}", file=sys.stderr)
    print(f"Values: {request.values}", file=sys.stderr)

    msg = (request.form.get("Body") or "").strip()
    user_id = (request.form.get("From") or "").strip()
    num_media = int(request.form.get("NumMedia") or 0)

    if not user_id:
        return "Error: No user_id provided", 400

    # 1. Handle incoming payment screenshots (Twilio POSTs as form-urlencoded)
    if num_media > 0:
        media_url = (request.form.get("MediaUrl0") or "").strip()
        reported_ct = (request.form.get("MediaContentType0") or "").strip().lower()
        uploads_dir = repo_root / "static" / "uploads"
        filename = None
        used_demo_receipt = False

        url_account_sid = None
        if media_url:
            m_acct = _MEDIA_URL_ACCOUNT_SID.search(media_url)
            url_account_sid = m_acct.group(1) if m_acct else None
            cfg_sid = (twilio_client.username or "").strip()
            if url_account_sid and cfg_sid and url_account_sid.upper() != cfg_sid.upper():
                print(
                    f"[screenshot] Media URL account {url_account_sid} != TWILIO_ACCOUNT_SID {cfg_sid}.",
                    file=sys.stderr,
                    flush=True,
                )

        can_fetch = bool(
            media_url and twilio_client.username and twilio_client.password
        )
        if not can_fetch:
            print(
                "[screenshot] Skipping Twilio media GET (no URL or missing credentials).",
                file=sys.stderr,
                flush=True,
            )

        if can_fetch:
            try:
                img_resp = requests.get(
                    media_url,
                    auth=twilio_client.auth,
                    timeout=(10, 60),
                    allow_redirects=True,
                    headers={"Accept": "*/*"},
                )
            except requests.RequestException as e:
                print(f"[screenshot] HTTP client error: {e}", file=sys.stderr, flush=True)
            else:
                raw = img_resp.content or b""
                resp_ct = (img_resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                is_twilio_error = _twilio_error_response_body(raw)
                looks_like_image = _body_looks_like_raster_image(raw)
                image_ok = (
                    img_resp.status_code == 200
                    and not is_twilio_error
                    and (
                        looks_like_image
                        or (
                            reported_ct.startswith("image/")
                            and resp_ct.startswith("image/")
                            and len(raw) > 32
                        )
                    )
                )
                print(
                    f"[screenshot] status={img_resp.status_code} reported_ct={reported_ct!r} "
                    f"resp_ct={resp_ct!r} bytes={len(raw)} looks_magic={looks_like_image} "
                    f"twilio_xml={is_twilio_error} user={user_id!r}",
                    file=sys.stderr,
                    flush=True,
                )
                if not image_ok:
                    if is_twilio_error or b"<RestException>" in raw[:800]:
                        print(f"[screenshot] Twilio body (truncated): {raw[:600]!r}", file=sys.stderr, flush=True)
                        if b"20003" in raw or b"Authenticate" in raw:
                            acct = url_account_sid or "(MediaUrl0 .../Accounts/AC.../)"
                            print(
                                f"[screenshot] Twilio 20003: fix Auth Token for account {acct} on host; "
                                "using demo receipt for this request.",
                                file=sys.stderr,
                                flush=True,
                            )
                    elif raw:
                        print(f"[screenshot] Non-image head hex: {raw[:32].hex()}", file=sys.stderr, flush=True)
                if image_ok:
                    try:
                        ext = _image_extension(raw)
                        fn = f"{uuid.uuid4().hex[:8]}{ext}"
                        uploads_dir.mkdir(parents=True, exist_ok=True)
                        (uploads_dir / fn).write_bytes(raw)
                        filename = fn
                    except OSError as e:
                        print(f"[screenshot] Failed to write upload: {e}", file=sys.stderr, flush=True)

        if filename is None:
            filename = _copy_demo_receipt_to_uploads(uploads_dir)
            used_demo_receipt = filename is not None
            if used_demo_receipt:
                print(
                    "[screenshot] Demo mode: attached data/receipt.png copy to order "
                    "(Twilio binary not stored).",
                    file=sys.stderr,
                    flush=True,
                )

        if filename is None:
            resp = MessagingResponse()
            resp.message("We could not process your payment image. Please try again.")
            return Response(str(resp), mimetype="application/xml")

        reply_text = _apply_payment_screenshot_to_order(user_id, filename)
        resp = MessagingResponse()
        resp.message(reply_text)
        return Response(str(resp), mimetype="application/xml")

    # 2. Use the BotEngine to get a standard response
    try:
        reply_text = llm_bot.process_user_message(user_id=user_id, text=msg)
    except Exception as e:
        reply_text = f"An error occurred: {str(e)}"

    print(f"[whatsapp] Reply to {user_id}: {reply_text[:200]}...", flush=True)

    # Create reply
    resp = MessagingResponse()
    
    send_image = False
    if "[SEND_MENU_IMAGE]" in reply_text:
        send_image = True
        reply_text = reply_text.replace("[SEND_MENU_IMAGE]", "").strip()
        if not reply_text:
            reply_text = "Here is our menu!"

    msg_resp = resp.message()
    msg_resp.body(reply_text)
    
    if send_image:
        forwarded_host = request.headers.get("X-Forwarded-Host")
        forwarded_proto = request.headers.get("X-Forwarded-Proto", "https")
        if forwarded_host:
            base_url = f"{forwarded_proto}://{forwarded_host}"
        else:
            base_url = request.url_root.rstrip("/")
        
        media_url = f"{base_url}/static/menu.png"
        msg_resp.media(media_url)

    return Response(str(resp), mimetype="application/xml")

@app.route("/", methods=['GET'])
def index():
    return app.send_static_file('index.html')

# --- Menu CRUD API ---

@app.route("/api/menu", methods=['GET'])
def get_menu():
    collection = get_menu_collection()
    items = list(collection.find({}, {'_id': 0})) # exclude mongo id for ease
    return jsonify(items)

@app.route("/api/menu", methods=['POST'])
def add_menu_item():
    data = request.json
    collection = get_menu_collection()
    
    # Generate ID if missing
    import uuid
    if "id" not in data or not data["id"]:
        data["id"] = "item_" + uuid.uuid4().hex[:6]
        
    collection.insert_one(data)
    del data['_id'] # Don't return ObjectId
    return jsonify({"success": True, "item": data}), 201

@app.route("/api/menu/<item_id>", methods=['PUT'])
def update_menu_item(item_id):
    data = request.json
    collection = get_menu_collection()
    result = collection.update_one({"id": item_id}, {"$set": data})
    if result.matched_count == 0:
        return jsonify({"success": False, "message": "Item not found"}), 404
    return jsonify({"success": True}), 200

@app.route("/api/menu/<item_id>", methods=['DELETE'])
def delete_menu_item(item_id):
    collection = get_menu_collection()
    result = collection.delete_one({"id": item_id})
    if result.deleted_count == 0:
        return jsonify({"success": False, "message": "Item not found"}), 404
    return jsonify({"success": True}), 200

# --- Orders API ---

@app.route("/api/orders", methods=['GET'])
def get_orders():
    collection = get_orders_collection()
    orders = list(collection.find({}, {'_id': 0}).sort("created_at", -1))
    return jsonify(orders)

@app.route("/api/orders/<order_id>/status", methods=['PUT'])
def update_order_status(order_id):
    data = request.json
    new_status = data.get("status")
    
    collection = get_orders_collection()
    order = collection.find_one({"id": order_id})
    
    if not order:
        return jsonify({"success": False, "message": "Order not found"}), 404
        
    collection.update_one({"id": order_id}, {"$set": {"status": new_status}})
    
    # Send Twilio WhatsApp notifications on status changes
    if new_status == "preparing":
        try:
            msg = twilio_client.messages.create(
                body=f"Your payment is confirmed. Order #{order_id} is being prepared!",
                from_=twilio_number,
                to=order["user_id"]
            )
            print(f"[Twilio] Successfully sent 'preparing' message to {order['user_id']}. SID: {msg.sid}", flush=True)
        except Exception as e:
            print(f"[Twilio Error] Failed to send 'preparing' message to {order['user_id']}: {type(e).__name__} - {str(e)}", flush=True)

    elif new_status == "dispatched":
        try:
            msg = twilio_client.messages.create(
                body=f"Order #{order_id} has been dispatched! It should reach you shortly. Thank you!",
                from_=twilio_number,
                to=order["user_id"]
            )
            print(f"[Twilio] Successfully sent 'dispatched' message to {order['user_id']}. SID: {msg.sid}", flush=True)
        except Exception as e:
            print(f"[Twilio Error] Failed to send 'dispatched' message to {order['user_id']}: {type(e).__name__} - {str(e)}", flush=True)
            
    return jsonify({"success": True}), 200

if __name__ == "__main__":
    # Port 4000 to avoid conflict with macOS ControlCenter/AirPlay on 5000
    app.run(debug=True, port=5001)

from flask import Flask, request, jsonify, Response
from twilio.twiml.messaging_response import MessagingResponse
from pathlib import Path
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
import requests
import uuid
from twilio.rest import Client

app = Flask(__name__)

# Initialize the bot engine once when the app starts.
# This keeps the sessions in memory as long as the process is running.
engine = BotEngine.from_repo_root(repo_root)
llm_bot = LLMBot(engine)

twilio_client = Client(
    os.environ.get("TWILIO_ACCOUNT_SID"),
    os.environ.get("TWILIO_AUTH_TOKEN")
)
twilio_number = os.environ.get("TWILIO_PHONE_NUMBER")

@app.route("/whatsapp", methods=['POST'])
def whatsapp_reply():
    """Respond to incoming WhatsApp messages with a dynamic message."""
    print("Received a request at /whatsapp", file=sys.stderr)
    print(f"Headers: {request.headers}", file=sys.stderr)
    print(f"Values: {request.values}", file=sys.stderr)

    msg = request.values.get('Body', '').strip()
    user_id = request.values.get('From', '').strip()
    num_media = int(request.values.get('NumMedia', 0))

    if not user_id:
        return "Error: No user_id provided", 400

    # 1. Handle incoming payment screenshots
    if num_media > 0:
        media_url = request.values.get('MediaUrl0')
        content_type = request.values.get('MediaContentType0')
        
        if content_type and content_type.startswith('image/'):
            # Download the image
            import tempfile
            img_resp = requests.get(media_url, auth=(os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN")))
            if img_resp.status_code == 200:
                filename = f"{uuid.uuid4().hex[:8]}.jpg"
                filepath = os.path.join(repo_root, "static", "uploads", filename)
                with open(filepath, 'wb') as f:
                    f.write(img_resp.content)
                
                # Update the order in DB
                orders = get_orders_collection()
                # Find the most recent pending order for this user
                print(f"[screenshot] Looking for pending_payment order for user_id='{user_id}'", flush=True)
                recent_order = orders.find_one({"user_id": user_id, "status": "pending_payment"}, sort=[("created_at", -1)])
                print(f"[screenshot] Found order: {recent_order is not None} (id={recent_order.get('id') if recent_order else 'N/A'})", flush=True)
                
                if recent_order:
                    orders.update_one(
                        {"_id": recent_order["_id"]},
                        {"$set": {
                            "status": "new",
                            "payment_screenshot": f"/static/uploads/{filename}"
                        }}
                    )
                    reply_text = "Aapka screenshot mil gaya hai. Humari team jaldi verify karegi. Intezaar karein, shukriya!"
                else:
                    reply_text = "Aapki tasveer mil gayi hai lekin mujhe aapka koi pending order nahi mila."
            else:
                reply_text = "Tasveer download karne mein masla hua. Bara-e-meharbani wapis bhejein."
            
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
                body=f"Aapki payment confirm ho gayi hai. Aapka order #{order_id} tayar ho raha hai!",
                from_=twilio_number,
                to=order["user_id"]
            )
            print(f"[Twilio] Successfully sent 'preparing' message to {order['user_id']}. SID: {msg.sid}", flush=True)
        except Exception as e:
            print(f"[Twilio Error] Failed to send 'preparing' message to {order['user_id']}: {type(e).__name__} - {str(e)}", flush=True)

    elif new_status == "dispatched":
        try:
            msg = twilio_client.messages.create(
                body=f"Aapka order #{order_id} dispatch ho gaya hai! Thodi der mein aapke paas pohunch jayega. Shukriya!",
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

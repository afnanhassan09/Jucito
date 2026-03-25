from purewaterbot.db import get_orders_collection

try:
    orders = list(get_orders_collection().find())
    print(f"Total orders: {len(orders)}")
    for o in orders:
        print(f"ID: {o.get('id')} - User: {o.get('user_id')} - Status: {o.get('status')} - Items: {len(o.get('items', []))}")
except Exception as e:
    print(f"Error: {e}")

import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGODB_CONNECTION_STRING", "mongodb://localhost:27017/")

client = MongoClient(MONGO_URI)
db = client.get_database("juicito")

menu_collection = db.get_collection("menu")
orders_collection = db.get_collection("orders")

def get_menu_collection():
    return menu_collection

def get_orders_collection():
    return orders_collection

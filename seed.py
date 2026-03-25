import json
import sys
from pathlib import Path
from purewaterbot.db import get_menu_collection


def seed(force=False):
    collection = get_menu_collection()
    existing = collection.count_documents({})

    if existing > 0 and not force:
        print(f"Database already has {existing} menu items. Use --force to drop and re-seed.")
        return

    menu_path = Path("data/menu.json")
    if not menu_path.exists():
        print("data/menu.json not found.")
        return

    try:
        raw = json.loads(menu_path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            items_data = raw
        else:
            items_data = raw.get("items", [])

        if not items_data:
            print("No items to seed.")
            return

        if force and existing > 0:
            collection.drop()
            print(f"Dropped existing {existing} menu items.")

        collection.insert_many(items_data)
        print(f"Successfully seeded {len(items_data)} items to MongoDB.")
    except Exception as e:
        print(f"Failed to seed: {e}")


if __name__ == "__main__":
    force = "--force" in sys.argv
    seed(force=force)

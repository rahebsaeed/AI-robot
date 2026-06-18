import json
import os

class PlacesMemory:
    def __init__(self, filepath=None):
        if filepath is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            filepath = os.path.join(project_root, "config", "places.json")
        self.filepath = filepath
        self.places = {}
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    self.places = json.load(f)
                print(f"[PLACES MEMORY] Loaded from {self.filepath}", flush=True)
            except Exception as e:
                print(f"[PLACES MEMORY ERROR] Load failed: {e}", flush=True)
                self.places = {}
        else:
            # Ensure config dir exists
            os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
            self.places = {}
            print(f"[PLACES MEMORY] Initialized new database at {self.filepath}", flush=True)

    def save(self):
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self.places, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[PLACES MEMORY ERROR] Save failed: {e}", flush=True)

    def save_place(self, map_name, place_name, x, y, yaw):
        place_name = place_name.lower().strip()
        if map_name not in self.places:
            self.places[map_name] = {}
        self.places[map_name][place_name] = {
            "x": round(float(x), 3),
            "y": round(float(y), 3),
            "yaw": round(float(yaw), 3)
        }
        self.save()

    def get_place(self, map_name, place_name):
        place_name = place_name.lower().strip()
        return self.places.get(map_name, {}).get(place_name)

    def remove_place(self, map_name, place_name):
        place_name = place_name.lower().strip()
        if map_name in self.places and place_name in self.places[map_name]:
            del self.places[map_name][place_name]
            self.save()
            return True
        return False

    def rename_place(self, map_name, old_name, new_name):
        old_name = old_name.lower().strip()
        new_name = new_name.lower().strip()
        if map_name in self.places and old_name in self.places[map_name]:
            self.places[map_name][new_name] = self.places[map_name].pop(old_name)
            self.save()
            return True
        return False

    def list_places(self, map_name):
        return list(self.places.get(map_name, {}).keys())

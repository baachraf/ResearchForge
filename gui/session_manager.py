"""
Session manager: save/load complete research sessions.
Each session captures queries, results, downloads, and summarize state.
"""
import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from gui.config_manager import SESSIONS_DIR


class SessionManager:
    def __init__(self):
        os.makedirs(SESSIONS_DIR, exist_ok=True)

    def list_sessions(self) -> List[Dict[str, Any]]:
        sessions = []
        if not os.path.isdir(SESSIONS_DIR):
            return sessions
        for fname in sorted(os.listdir(SESSIONS_DIR), reverse=True):
            if fname.endswith(".json"):
                path = os.path.join(SESSIONS_DIR, fname)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    sessions.append({
                        "id": fname.replace(".json", ""),
                        "name": data.get("name", "Unnamed"),
                        "topic": data.get("topic", ""),
                        "created": data.get("created", ""),
                        "query_count": len(data.get("queries", [])),
                        "result_count": len(data.get("results", [])),
                        "path": path,
                    })
                except Exception:
                    continue
        return sessions

    def save(self, session_id: str, data: Dict[str, Any]):
        data["created"] = data.get("created") or datetime.now().strftime("%Y-%m-%d %H:%M")
        data["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return path

    def load(self, session_id: str) -> Optional[Dict[str, Any]]:
        path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def delete(self, session_id: str):
        path = os.path.join(SESSIONS_DIR, f"{session_id}.json")
        if os.path.isfile(path):
            os.remove(path)

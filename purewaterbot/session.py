from __future__ import annotations

from .models import ChatMessage, Session


class SessionManager:
    """
    In-memory session store keyed by user_id.
    Phase 1: no persistence, no expiry.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, user_id: str) -> Session:
        if user_id not in self._sessions:
            self._sessions[user_id] = Session(user_id=user_id)
        return self._sessions[user_id]

    def append_history(self, user_id: str, role: str, content: str) -> None:
        s = self.get_or_create(user_id)
        s.history.append(ChatMessage(role=role, content=content))

    def reset(self, user_id: str) -> Session:
        self._sessions[user_id] = Session(user_id=user_id)
        return self._sessions[user_id]

    def export_session(self, user_id: str) -> dict:
        """
        Useful for debugging from the mock interface.
        """
        s = self.get_or_create(user_id)
        # Convert dataclasses safely
        return {
            "user_id": s.user_id,
            "cart": [ci.to_dict() for ci in s.cart],
            "address": s.address,
            "history": [m.to_dict() for m in s.history],
        }


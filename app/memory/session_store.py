from threading import RLock

from app.models.schemas import ConversationSession


class SessionNotFoundError(KeyError):
    pass


class SessionBusyError(RuntimeError):
    pass


class SessionStore:
    """Thread-safe, process-local conversation store."""

    def __init__(self) -> None:
        self._sessions: dict[str, ConversationSession] = {}
        self._active: set[str] = set()
        self._lock = RLock()

    def create(self) -> ConversationSession:
        session = ConversationSession()
        with self._lock:
            self._sessions[session.session_id] = session
        return session.model_copy(deep=True)

    def list(self) -> list[ConversationSession]:
        with self._lock:
            sessions = sorted(self._sessions.values(), key=lambda item: item.updated_at, reverse=True)
            return [session.model_copy(deep=True) for session in sessions]

    def get(self, session_id: str) -> ConversationSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            return session.model_copy(deep=True)

    def save(self, session: ConversationSession) -> None:
        with self._lock:
            if session.session_id not in self._sessions:
                raise SessionNotFoundError(session.session_id)
            self._sessions[session.session_id] = session.model_copy(deep=True)

    def delete(self, session_id: str) -> None:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)
            self._sessions.pop(session_id)
            self._active.discard(session_id)

    def acquire(self, session_id: str) -> None:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)
            if session_id in self._active:
                raise SessionBusyError(session_id)
            self._active.add(session_id)

    def release(self, session_id: str) -> None:
        with self._lock:
            self._active.discard(session_id)

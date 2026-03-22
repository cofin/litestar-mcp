"""MCP session management for Streamable HTTP transport."""

import secrets
from typing import Any, Optional


class MCPSessionManager:
    """Manages MCP sessions.

    Each session is identified by a cryptographically secure token assigned
    during the ``initialize`` handshake and validated on subsequent requests.

    Attributes:
        _sessions: Mapping from session ID to session metadata.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}

    def create_session(self, metadata: Optional[dict[str, Any]] = None) -> str:
        """Create a new session and return its ID.

        Args:
            metadata: Optional metadata to store with the session.

        Returns:
            A cryptographically secure session ID.
        """
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = metadata or {}
        return session_id

    def validate_session(self, session_id: str) -> bool:
        """Check whether a session ID is valid (exists and has not been terminated).

        Args:
            session_id: The session ID to validate.

        Returns:
            True if valid, False otherwise.
        """
        return session_id in self._sessions

    def terminate_session(self, session_id: str) -> None:
        """Terminate a session.

        Args:
            session_id: The session ID to terminate.
        """
        self._sessions.pop(session_id, None)

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        """Retrieve session metadata.

        Args:
            session_id: The session ID.

        Returns:
            Session metadata dict, or None if the session does not exist.
        """
        return self._sessions.get(session_id)

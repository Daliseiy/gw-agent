import base64
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.core.config import settings


logger = logging.getLogger(__name__)


SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/tasks",
]


@dataclass
class AuthenticatedUser:
    session_id: str
    user_id: str
    email: str
    full_name: str
    given_name: str
    family_name: str
    picture: str
    credentials: Credentials

    def public_profile(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "email": self.email,
            "full_name": self.full_name,
            "given_name": self.given_name,
            "family_name": self.family_name,
            "picture": self.picture,
        }


class AuthManager:
    """Google OAuth auth/session manager with lightweight file-backed session persistence."""

    def __init__(self) -> None:
        self.client_secrets_file = settings.google_client_secrets_file
        self.redirect_uri = settings.google_oauth_redirect_uri
        self._session_store_path = Path(settings.auth_session_store_file)
        self._pending_states: dict[str, dict[str, str | None]] = {}
        self._sessions: dict[str, AuthenticatedUser] = self._load_sessions()

    def _build_flow(self, *, state: str | None = None) -> Flow:
        flow = Flow.from_client_secrets_file(
            self.client_secrets_file,
            scopes=SCOPES,
            state=state,
        )
        flow.redirect_uri = self.redirect_uri
        return flow

    def _sanitize_return_to(self, return_to: str | None) -> str | None:
        if not return_to:
            return None
        parsed = urlparse(return_to)
        if parsed.scheme or parsed.netloc:
            return None
        if not return_to.startswith("/"):
            return None
        return return_to

    def create_authorization_url(self, return_to: str | None = None) -> dict[str, str]:
        state = secrets.token_urlsafe(32)
        flow = self._build_flow(state=state)

        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)

        authorization_url, returned_state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            code_challenge=code_challenge,
            code_challenge_method="S256",
        )

        self._pending_states[returned_state] = {
            "return_to": self._sanitize_return_to(return_to),
            "code_verifier": code_verifier,
        }
        logger.info("OAuth authorization URL created | state=%s return_to=%s", returned_state[:8], return_to or "/assistant")

        response = {"authorization_url": authorization_url, "state": returned_state}
        if return_to:
            response["return_to"] = return_to
        return response

    def exchange_code_for_user(self, code: str, state: str) -> tuple[AuthenticatedUser, str | None]:
        pending_state = self._pending_states.get(state)
        if pending_state is None:
            raise ValueError("Invalid or expired OAuth state.")

        code_verifier = pending_state.get("code_verifier")
        if not code_verifier:
            raise ValueError("Missing PKCE code verifier for OAuth flow.")

        flow = self._build_flow(state=state)
        flow.code_verifier = code_verifier
        flow.fetch_token(code=code)

        credentials = flow.credentials
        userinfo_service = build("oauth2", "v2", credentials=credentials)
        profile = userinfo_service.userinfo().get().execute()

        session_id = secrets.token_urlsafe(32)
        user = AuthenticatedUser(
            session_id=session_id,
            user_id=profile.get("id", ""),
            email=profile.get("email", ""),
            full_name=profile.get("name", ""),
            given_name=profile.get("given_name", ""),
            family_name=profile.get("family_name", ""),
            picture=profile.get("picture", ""),
            credentials=credentials,
        )
        self._sessions[session_id] = user
        self._pending_states.pop(state, None)
        self._persist_sessions()
        logger.info("OAuth exchange success | user=%s session_id=%s", user.email, session_id[:8])
        return user, pending_state.get("return_to")

    def get_authenticated_user(self, session_id: str) -> AuthenticatedUser | None:
        user = self._sessions.get(session_id)
        if not user:
            return None
        if self._refresh_if_needed(user.credentials):
            self._persist_sessions()
        return user

    def _refresh_if_needed(self, credentials: Credentials) -> bool:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            return True
        return False

    def revoke_session(self, session_id: str) -> bool:
        revoked = self._sessions.pop(session_id, None) is not None
        if revoked:
            self._persist_sessions()
            logger.info("Session revoked | session_id=%s", session_id[:8])
        return revoked

    def _load_sessions(self) -> dict[str, AuthenticatedUser]:
        if not self._session_store_path.exists():
            return {}
        try:
            raw_payload = json.loads(self._session_store_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read auth session store: %s", exc)
            return {}

        raw_sessions = raw_payload.get("sessions", []) if isinstance(raw_payload, dict) else []
        if not isinstance(raw_sessions, list):
            return {}

        sessions: dict[str, AuthenticatedUser] = {}
        for raw_session in raw_sessions:
            user = self._deserialize_user(raw_session)
            if user:
                sessions[user.session_id] = user
        return sessions

    def _serialize_user(self, user: AuthenticatedUser) -> dict[str, Any]:
        return {
            "session_id": user.session_id,
            "user_id": user.user_id,
            "email": user.email,
            "full_name": user.full_name,
            "given_name": user.given_name,
            "family_name": user.family_name,
            "picture": user.picture,
            "credentials": json.loads(user.credentials.to_json()),
        }

    def _deserialize_user(self, payload: Any) -> AuthenticatedUser | None:
        if not isinstance(payload, dict):
            return None
        session_id = payload.get("session_id")
        credentials_payload = payload.get("credentials")
        if not session_id or not isinstance(credentials_payload, dict):
            return None

        try:
            scopes = credentials_payload.get("scopes") if isinstance(credentials_payload.get("scopes"), list) else SCOPES
            credentials = Credentials.from_authorized_user_info(credentials_payload, scopes=scopes)
        except Exception as exc:
            logger.warning("Failed to decode stored credentials for session %s: %s", session_id, exc)
            return None

        return AuthenticatedUser(
            session_id=session_id,
            user_id=payload.get("user_id", ""),
            email=payload.get("email", ""),
            full_name=payload.get("full_name", ""),
            given_name=payload.get("given_name", ""),
            family_name=payload.get("family_name", ""),
            picture=payload.get("picture", ""),
            credentials=credentials,
        )

    def _persist_sessions(self) -> None:
        try:
            self._session_store_path.parent.mkdir(parents=True, exist_ok=True)
            serialized_sessions = [self._serialize_user(user) for user in self._sessions.values()]
            payload = {"sessions": serialized_sessions}
            temp_path = self._session_store_path.with_suffix(f"{self._session_store_path.suffix}.tmp")
            temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            temp_path.replace(self._session_store_path)
        except Exception as exc:
            logger.warning("Failed to persist auth sessions: %s", exc)

    def _generate_code_verifier(self) -> str:
        return secrets.token_urlsafe(64)

    def _generate_code_challenge(self, code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("utf-8")


auth_manager = AuthManager()

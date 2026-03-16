from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.project_root = Path(__file__).resolve().parents[2]
        load_dotenv(self.project_root / ".env")

        self.app_name = os.getenv("APP_NAME", "Google Workspace Voice AI Assistant")
        self.environment = os.getenv("APP_ENV", "development")
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        self.log_http_requests = _env_bool("LOG_HTTP_REQUESTS", True)
        self.log_audio_chunks = _env_bool("LOG_AUDIO_CHUNKS", False)
        self.log_tool_payloads = _env_bool("LOG_TOOL_PAYLOADS", True)

        cors_allow_origins = os.getenv("CORS_ALLOW_ORIGINS", "*")
        self.cors_allow_origins = [origin.strip() for origin in cors_allow_origins.split(",") if origin.strip()] or ["*"]

        self.templates_dir = self.project_root / "app" / "templates"
        self.static_dir = self.project_root / "app" / "static"

        self.google_client_secrets_file = os.getenv(
            "GOOGLE_CLIENT_SECRETS_FILE",
            str(self.project_root / "credentials.json"),
        )
        self.google_oauth_redirect_uri = os.getenv(
            "GOOGLE_OAUTH_REDIRECT_URI",
            "http://localhost:8000/auth/google/callback",
        )
        self.auth_session_store_file = os.getenv(
            "AUTH_SESSION_STORE_FILE",
            str(self.project_root / ".auth_sessions.json"),
        )
        self.gemini_api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self.gemini_use_vertex_ai = _env_bool("GEMINI_USE_VERTEX_AI", _env_bool("GOOGLE_GENAI_USE_VERTEXAI", False))
        self.vertex_ai_project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("VERTEX_PROJECT_ID")
        self.vertex_ai_location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

        default_model = "gemini-2.0-flash-live-preview-04-09" if self.gemini_use_vertex_ai else "gemini-live-2.5-flash-preview"
        configured_model = os.getenv("GEMINI_MODEL", default_model)
        self.gemini_model = configured_model[len("models/") :] if configured_model.startswith("models/") else configured_model


settings = Settings()

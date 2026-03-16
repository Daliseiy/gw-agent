from urllib.parse import urlencode
import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.services.auth import auth_manager


router = APIRouter(tags=["auth"])
logger = logging.getLogger(__name__)


@router.get("/auth/google/login")
def google_login(return_to: str | None = Query(default=None)):
    """Returns the Google OAuth consent URL the frontend should open."""
    logger.info("Auth login requested | return_to=%s", return_to or "/assistant")
    return auth_manager.create_authorization_url(return_to=return_to)


@router.get("/auth/google/callback")
def google_callback(code: str = Query(...), state: str = Query(...)):
    """Exchanges the OAuth authorization code for user credentials and profile."""
    logger.info("Auth callback received | state=%s", state[:8] + "..." if len(state) > 8 else state)
    try:
        user, return_to = auth_manager.exchange_code_for_user(code=code, state=state)
    except ValueError as exc:
        logger.warning("Auth callback rejected | reason=%s", str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Auth callback failed | reason=%s", str(exc))
        raise HTTPException(status_code=500, detail=f"Google authentication failed: {str(exc)}") from exc

    logger.info("Auth callback success | user=%s return_to=%s", user.email, return_to or "(profile)")
    if return_to:
        separator = "&" if "?" in return_to else "?"
        redirect_target = f"{return_to}{separator}{urlencode({'session_id': user.session_id})}"
        return RedirectResponse(url=redirect_target, status_code=303)
    return user.public_profile()


@router.get("/me")
def get_current_user(session_id: str = Query(...)):
    user = auth_manager.get_authenticated_user(session_id)
    if not user:
        logger.warning("Auth me lookup failed | session_id=%s", session_id[:6] + "..." if len(session_id) > 6 else "***")
        raise HTTPException(status_code=401, detail="Invalid or expired session.")
    logger.debug("Auth me lookup success | user=%s", user.email)
    return user.public_profile()


@router.post("/logout")
def logout(session_id: str = Query(...)):
    if not auth_manager.revoke_session(session_id):
        logger.warning("Auth logout failed | session not found")
        raise HTTPException(status_code=404, detail="Session not found.")
    logger.info("Auth logout success")
    return {"status": "logged_out"}

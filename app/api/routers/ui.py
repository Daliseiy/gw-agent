from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings


router = APIRouter(tags=["ui"])
TEMPLATES_DIR: Path = settings.templates_dir


def _template_or_404(filename: str, detail: str) -> FileResponse:
    template_path = TEMPLATES_DIR / filename
    if not template_path.exists():
        raise HTTPException(status_code=404, detail=detail)
    return FileResponse(template_path)


@router.get("/")
def root():
    return _template_or_404("index.html", "UI not found")


@router.get("/ui")
def ui():
    return _template_or_404("index.html", "UI not found")


@router.get("/assistant")
def assistant_ui():
    return _template_or_404("assistant.html", "Assistant UI not found")


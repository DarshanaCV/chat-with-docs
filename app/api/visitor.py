from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse

from app.api.upload import IMAGE_EXTENSIONS
from app.services.visitor_scope import COOKIE_NAME, DEMO_MODE, new_visitor_token, visitor_namespace, visitor_prefix

router = APIRouter()
ASSET_DIRECTORIES = {"uploads", "figures"}


@router.get("/visitor", status_code=204)
def start_visitor(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if DEMO_MODE and visitor_namespace(request.cookies.get(COOKIE_NAME)) is None:
        response.set_cookie(
            COOKIE_NAME, new_visitor_token(), max_age=30 * 24 * 60 * 60,
            secure=True, httponly=True, samesite="lax", path="/",
        )


@router.get("/data/{directory}/{filename}")
def visitor_asset(directory: str, filename: str, prefix: str = Depends(visitor_prefix)):
    if (
        directory not in ASSET_DIRECTORIES
        or not filename.startswith(prefix)
        or "/" in filename
        or "\\" in filename
        or Path(filename).suffix.lower() not in IMAGE_EXTENSIONS
    ):
        raise HTTPException(status_code=404, detail="Image not found")
    folder = (Path("data") / directory).resolve()
    path = (folder / filename).resolve()
    if path.parent != folder or not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, headers={"X-Content-Type-Options": "nosniff"})

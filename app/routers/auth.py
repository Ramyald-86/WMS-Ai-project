from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import read_session_token, session_principal
from app.core.config import settings
from app.core.database import get_db
from app.services.fulfillment_auth import login_for_host, session_public_view

router = APIRouter(prefix="/auth", tags=["Вход"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "fc_session",
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="lax",
        path="/",
    )


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """Host-aware login.

    Main/apex domain: unchanged platform admin login.
    Fulfillment subdomain/custom domain: FulfillmentUser of that FC (or platform admin).
    """
    resolution = getattr(request.state, "tenant_resolution", None)
    result = login_for_host(
        db,
        username=payload.username,
        password=payload.password,
        resolution=resolution,
    )
    _set_session_cookie(response, result["token"])
    body = {k: v for k, v in result.items() if k != "token"}
    return body


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("fc_session", path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    # Prefer middleware-hydrated state when present; fall back to cookie parse.
    if getattr(request.state, "username", None):
        view = {
            "username": request.state.username,
            "principal": getattr(request.state, "auth_principal", None),
            "role": getattr(request.state, "auth_role", None),
            "fulfillment_id": getattr(request.state, "auth_fulfillment_id", None),
            "user_id": getattr(request.state, "auth_user_id", None),
        }
    else:
        session = read_session_token(request.cookies.get("fc_session"), settings.app_secret_key)
        if not session:
            raise HTTPException(status_code=401, detail="Требуется вход")
        view = session_public_view(session)
        if session_principal(session) == "platform" and not view.get("role"):
            view["role"] = "PLATFORM_ADMIN"
    # Attach modular permissions (TZ §1.3) when schema is available.
    try:
        from app.services.zipline_permissions import modules_for_permissions, permissions_for_role
        from app.services.zipline_tz_core import install_zipline_tz_schema
        from sqlalchemy import text

        role = view.get("role")
        if role == "PLATFORM_ADMIN" or view.get("principal") == "platform":
            perms = permissions_for_role("PLATFORM_ADMIN")
        elif view.get("user_id"):
            install_zipline_tz_schema(db)
            row = db.execute(
                text("SELECT role, permissions FROM fulfillment_users WHERE id = CAST(:id AS uuid)"),
                {"id": view["user_id"]},
            ).mappings().first()
            perms = permissions_for_role((row or {}).get("role") or role, (row or {}).get("permissions"))
        else:
            perms = permissions_for_role(role)
        view["permissions"] = perms
        view["modules"] = modules_for_permissions(perms)
    except Exception:
        pass
    return view

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.core.auth import create_session_token, read_session_token, verify_password
from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["Вход"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


@router.post("/login")
def login(payload: LoginRequest, response: Response):
    if not settings.admin_password_hash:
        raise HTTPException(status_code=503, detail="Пароль администратора не настроен")
    ok_user = payload.username.strip() == settings.admin_username
    ok_pass = verify_password(payload.password, settings.admin_password_hash)
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = create_session_token(settings.admin_username, settings.app_secret_key, settings.session_ttl_hours)
    response.set_cookie(
        "fc_session",
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="lax",
        path="/",
    )
    return {"ok": True, "username": settings.admin_username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie("fc_session", path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    session = read_session_token(request.cookies.get("fc_session"), settings.app_secret_key)
    if not session:
        raise HTTPException(status_code=401, detail="Требуется вход")
    return {"username": session["u"]}

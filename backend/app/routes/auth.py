"""Signing in and out.

Sessions are HttpOnly cookies, so file downloads and event streams carry them
without the page ever handling a token. A Bearer header is accepted too, for
API clients.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DB

from ..db import models as m
from ..db import repository as repo
from ..deps import current_user, get_db, request_token
from ..schemas import DemoAccountOut, DemoIn, LoginIn, UserOut, user_out
from ..services.auth import (
    ADMIN,
    COOKIE,
    FACULTY,
    SESSION_LIFETIME,
    STUDENT,
    demo_login_enabled,
    secure_cookies,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# The accounts a one-click demo sign-in picks by default. Prof. Mehta is the
# teacher in the rehearsed disruption.
DEFAULT_DEMO = {ADMIN: "admin", FACULTY: "mehta", STUDENT: "student"}


def _start_session(response: Response, db: DB, user: m.UserRow) -> UserOut:
    token = repo.open_session(db, user)
    db.commit()
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=secure_cookies(),
        max_age=int(SESSION_LIFETIME.total_seconds()),
        path="/",
    )
    return user_out(user)


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, response: Response, db: DB = Depends(get_db)) -> UserOut:
    user = repo.authenticate(db, body.username.strip(), body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    return _start_session(response, db, user)


@router.get("/demo-accounts", response_model=list[DemoAccountOut])
def demo_accounts(db: DB = Depends(get_db)) -> list[DemoAccountOut]:
    """Accounts offered for one-click sign-in. Empty when demo sign-in is off."""
    if not demo_login_enabled():
        return []
    return [
        DemoAccountOut(role=u.role, username=u.username, display_name=u.display_name)
        for u in repo.list_users(db)
        if u.active
    ]


@router.post("/demo", response_model=UserOut)
def demo_login(body: DemoIn, response: Response, db: DB = Depends(get_db)) -> UserOut:
    if not demo_login_enabled():
        raise HTTPException(status_code=404, detail="Demo sign-in is switched off.")
    wanted = (body.username or DEFAULT_DEMO[body.role]).lower()
    user = repo.user_by_username(db, wanted)
    if user is None and body.username is None:
        user = next(
            (u for u in repo.list_users(db) if u.role == body.role and u.active), None
        )
    if user is None or user.role != body.role or not user.active:
        raise HTTPException(
            status_code=404,
            detail=f"No {body.role.title()} demo account named {wanted!r}.",
        )
    return _start_session(response, db, user)


@router.post("/logout")
def logout(request: Request, response: Response, db: DB = Depends(get_db)) -> dict:
    token = request_token(request)
    if token:
        repo.close_session(db, token)
        db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"signed_out": True}


@router.get("/me", response_model=UserOut)
def me(user: m.UserRow = Depends(current_user)) -> UserOut:
    return user_out(user)

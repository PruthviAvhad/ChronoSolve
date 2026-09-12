"""Request plumbing: the database handle, the signed-in user, role guards,
service-error translation, and server-sent-event streaming."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session as DB

from .db import models as m
from .db import repository as repo
from .db.seed import seed_if_empty
from .db.session import database_url, make_engine, migrate, session_factory
from .progress import ProgressLog
from .services.auth import ADMIN, COOKIE, FACULTY
from .services.scheduling import ServiceError

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------


class Database:
    """The application's database handle. Tests point it at a scratch file."""

    def __init__(self) -> None:
        self.engine = None
        self.factory = None
        self.url: str | None = None

    def configure(
        self,
        url: str | None = None,
        *,
        run_migrations: bool = True,
        seeder: Callable[[DB], object] | None = None,
    ) -> None:
        self.dispose()
        self.url = url or database_url()
        self.engine = make_engine(self.url)
        if run_migrations:
            migrate(self.engine)
        self.factory = session_factory(self.engine)
        with self.factory() as db:
            if seeder is not None:
                seeder(db)
            else:
                seed_if_empty(db)

    def dispose(self) -> None:
        if self.engine is not None:
            self.engine.dispose()
        self.engine = None
        self.factory = None

    def session(self) -> DB:
        if self.factory is None:
            self.configure()
        return self.factory()


DATABASE = Database()


def get_db() -> Iterator[DB]:
    db = DATABASE.session()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------------
# Who is asking
# --------------------------------------------------------------------------


def request_token(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    if token:
        return token
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def current_user(request: Request, db: DB = Depends(get_db)) -> m.UserRow:
    token = request_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    user = repo.user_for_token(db, token)
    if user is None:
        db.commit()  # an expired session was removed
        raise HTTPException(status_code=401, detail="Your session has ended. Sign in again.")
    return user


def require(*roles: str):
    readable = " or ".join(r.title() for r in roles)

    def guard(user: m.UserRow = Depends(current_user)) -> m.UserRow:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail=f"This needs the {readable} role.")
        return user

    return guard


ADMIN_ONLY = require(ADMIN)
STAFF = require(ADMIN, FACULTY)


@contextmanager
def service_errors() -> Iterator[None]:
    """Translate a service refusal into the HTTP response it describes."""
    try:
        yield
    except ServiceError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


# --------------------------------------------------------------------------
# Streaming
# --------------------------------------------------------------------------
#
# A solve takes tens of seconds, so a single blocking POST leaves the UI with
# nothing honest to show. Streaming routes run the *same* functions as the
# plain POSTs and relay the stage boundaries as they are reached. No progress
# percentage is sent, because CP-SAT cannot say how much search is left; what
# is sent is the step now running and the measured duration of each finished
# step, ending with the identical payload the plain POST would have returned.

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # stop nginx-style proxies buffering the stream
}
HEARTBEAT_SECONDS = 15.0


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def stream_solve(work: Callable[[ProgressLog], BaseModel]) -> AsyncIterator[str]:
    """Run `work` on a worker thread, relaying its stages as they happen.

    The solver is synchronous and CPU-bound, so it cannot yield to the event
    loop; running it on a thread is what lets the stages reach the client while
    the search is still going. `work` must open its own database session:
    sessions are not shared between threads.
    """
    events: queue.Queue = queue.Queue()
    outcome: dict = {}

    def run() -> None:
        try:
            outcome["result"] = work(ProgressLog(listener=events.put))
        except HTTPException as exc:
            outcome["error"] = {"status": exc.status_code, "detail": str(exc.detail)}
        except ServiceError as exc:
            outcome["error"] = {"status": exc.status, "detail": exc.detail}
        except Exception as exc:  # noqa: BLE001 - surfaced to the client below
            outcome["error"] = {"status": 500, "detail": str(exc)}
        finally:
            events.put(None)  # sentinel: the work is over either way

    worker = threading.Thread(target=run, name="chronosolve-solve", daemon=True)
    worker.start()

    last_sent = asyncio.get_running_loop().time()
    while True:
        try:
            item = events.get_nowait()
        except queue.Empty:
            now = asyncio.get_running_loop().time()
            if now - last_sent >= HEARTBEAT_SECONDS:
                last_sent = now
                yield ": still solving\n\n"  # comment frame, ignored by the client
            await asyncio.sleep(0.05)
            continue
        if item is None:
            break
        last_sent = asyncio.get_running_loop().time()
        yield sse(item)

    worker.join()
    if "error" in outcome:
        yield sse({"event": "error", **outcome["error"]})
    else:
        yield sse({"event": "result", "data": outcome["result"].model_dump(mode="json")})

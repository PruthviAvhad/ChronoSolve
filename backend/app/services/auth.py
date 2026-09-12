"""Authentication and roles, deliberately small.

Passwords are stored as salted PBKDF2-SHA256 from the standard library, and
sign-in tokens only as SHA-256 digests, so a leaked database reveals neither.
This is an access-control layer for a scheduling tool, not an identity
platform: three roles, cookie sessions, nothing more.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import timedelta

ADMIN = "ADMIN"  # timetable coordinator
FACULTY = "FACULTY"
STUDENT = "STUDENT"
ROLES = (ADMIN, FACULTY, STUDENT)

SESSION_LIFETIME = timedelta(hours=12)
COOKIE = "chronosolve_session"


def _iterations() -> int:
    # The test suite lowers this so it can create accounts quickly.
    return int(os.getenv("CHRONOSOLVE_PBKDF2_ITERATIONS", "240000"))


def hash_password(password: str) -> str:
    iterations = _iterations()
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
    )
    return hmac.compare_digest(candidate.hex(), digest)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def demo_login_enabled() -> bool:
    """One-click demo sign-in. On by default for the hackathon build; set
    CHRONOSOLVE_DEMO_LOGIN=0 to require passwords everywhere."""
    return os.getenv("CHRONOSOLVE_DEMO_LOGIN", "1") != "0"


def secure_cookies() -> bool:
    return os.getenv("CHRONOSOLVE_SECURE_COOKIES", "0") == "1"

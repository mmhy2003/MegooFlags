"""argon2id password hashing and the password policy: 12 to 128 characters, not on the bundled
common password list, no composition rules."""

import functools
import secrets
from importlib import resources

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from megooflags_api.errors import weak_password

MIN_LENGTH = 12
MAX_LENGTH = 128

_hasher = PasswordHasher()


@functools.cache
def _dummy_hash() -> str:
    return _hasher.hash(secrets.token_urlsafe(32))


@functools.cache
def _common_passwords() -> frozenset[str]:
    text = resources.files(__package__).joinpath("data/common_passwords.txt").read_text("utf-8")
    return frozenset(line for line in text.splitlines() if line and not line.startswith("#"))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(hashed: str | None, password: str) -> bool:
    """Check a password. Without a hash it still runs a verification, so the timing doesn't
    reveal whether the account exists or has a password."""
    try:
        _hasher.verify(_dummy_hash() if hashed is None else hashed, password)
    except (VerificationError, InvalidHashError):
        return False
    return hashed is not None


def needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


def check_password_policy(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise weak_password(f"the password must be at least {MIN_LENGTH} characters long")
    if len(password) > MAX_LENGTH:
        raise weak_password(f"the password must be at most {MAX_LENGTH} characters long")
    if password.lower() in _common_passwords():
        raise weak_password("the password is too common; choose another one")

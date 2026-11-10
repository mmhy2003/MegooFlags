import pytest
from argon2 import PasswordHasher

from megooflags_api.auth.passwords import (
    check_password_policy,
    hash_password,
    needs_rehash,
    verify_password,
)
from megooflags_api.errors import ApiError


def test_hash_is_argon2id() -> None:
    hashed = hash_password("correct horse battery staple")

    assert hashed.startswith("$argon2id$")
    assert not needs_rehash(hashed)


def test_verify_password() -> None:
    hashed = hash_password("correct horse battery staple")

    assert verify_password(hashed, "correct horse battery staple")
    assert not verify_password(hashed, "correct horse battery stapler")
    assert not verify_password("not-a-hash", "correct horse battery staple")


def test_verify_without_hash_still_runs_the_hasher(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real_verify = PasswordHasher.verify

    def spy(self: PasswordHasher, hashed: str, password: str) -> bool:
        calls.append(hashed)
        return real_verify(self, hashed, password)

    monkeypatch.setattr(PasswordHasher, "verify", spy)

    assert not verify_password(None, "correct horse battery staple")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "password",
    ["a" * 11, "a" * 129, "passwordpassword", "PasswordPassword", "MEGOOFLAGSADMIN"],
)
def test_policy_rejects_weak_passwords(password: str) -> None:
    with pytest.raises(ApiError) as excinfo:
        check_password_policy(password)

    assert excinfo.value.status == 422
    assert excinfo.value.code == "weak_password"
    assert excinfo.value.details == {"path": "password"}


def test_policy_accepts_a_passphrase() -> None:
    check_password_policy("correct horse battery staple")

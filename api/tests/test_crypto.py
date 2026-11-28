import pytest

from megooflags_api.crypto import SecretBox

KEY = "a" * 32


def test_round_trip() -> None:
    box = SecretBox(KEY)

    assert box.decrypt(box.encrypt("client-secret")) == "client-secret"


def test_another_secret_cannot_decrypt() -> None:
    token = SecretBox(KEY).encrypt("client-secret")

    with pytest.raises(ValueError):
        SecretBox("b" * 32).decrypt(token)


def test_ciphertext_is_not_the_plaintext() -> None:
    token = SecretBox(KEY).encrypt("client-secret")

    assert "client-secret" not in token

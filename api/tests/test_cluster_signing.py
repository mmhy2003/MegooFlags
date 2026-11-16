import base64

from megooflags_api.main import create_app
from megooflags_api.services.cluster.signing import SnapshotSigner, verify
from megooflags_api.settings import Settings

SECRET = "s" * 32


def test_same_secret_same_key() -> None:
    first, second = SnapshotSigner(SECRET), SnapshotSigner(SECRET)

    assert first.public_key == second.public_key
    assert len(base64.b64decode(first.public_key)) == 32
    assert verify(second.public_key, b"data", first.sign(b"data"))


def test_other_secret_other_key() -> None:
    assert SnapshotSigner(SECRET).public_key != SnapshotSigner("t" * 32).public_key


def test_sign_and_verify() -> None:
    signer = SnapshotSigner(SECRET)

    signature = signer.sign(b"payload")

    assert len(base64.b64decode(signature, validate=True)) == 64
    assert verify(signer.public_key, b"payload", signature)


def test_tampered_data_fails() -> None:
    signer = SnapshotSigner(SECRET)
    signature = signer.sign(b"payload")

    assert not verify(signer.public_key, b"payloaD", signature)
    assert not verify(SnapshotSigner("t" * 32).public_key, b"payload", signature)


def test_verify_never_raises() -> None:
    signer = SnapshotSigner(SECRET)
    signature = signer.sign(b"payload")
    short_key = base64.b64encode(b"x" * 5).decode()

    assert not verify("not base64!", b"payload", signature)
    assert not verify(short_key, b"payload", signature)
    assert not verify("", b"payload", signature)
    assert not verify(signer.public_key, b"payload", "not base64!")
    assert not verify(signer.public_key, b"payload", base64.b64encode(b"x" * 3).decode())
    assert not verify(signer.public_key, b"payload", "")
    assert not verify("é", b"payload", "é")


async def test_create_app_has_snapshot_signer_with_key() -> None:
    app = create_app(Settings(secret_key=SECRET))
    try:
        assert app.state.snapshot_signer.public_key == SnapshotSigner(SECRET).public_key
    finally:
        await app.state.valkey.aclose()
        await app.state.engine.dispose()


async def test_create_app_has_no_snapshot_signer_without_key() -> None:
    app = create_app(Settings(secret_key=None))
    try:
        assert app.state.snapshot_signer is None
    finally:
        await app.state.valkey.aclose()
        await app.state.engine.dispose()

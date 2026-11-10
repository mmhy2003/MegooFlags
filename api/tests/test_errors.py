import httpx
from fastapi import FastAPI
from sqlalchemy import text

from megooflags_api.errors import integrity_error_status_code, not_found
from megooflags_api.main import create_app
from megooflags_api.schemas.common import ApiModel, Key


class Widget(ApiModel):
    key: Key


async def test_api_error_body() -> None:
    app = create_app()

    @app.get("/test/missing")
    async def missing() -> None:
        raise not_found("flag", "x")

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/test/missing")
    finally:
        await app.state.engine.dispose()

    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "flag 'x' not found",
        "details": None,
    }


async def test_request_validation_uses_error_body() -> None:
    app = create_app()

    @app.post("/test/widgets")
    async def create_widget(body: Widget) -> Widget:
        return body

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/test/widgets", json={"key": "a", "colour": "red"})
    finally:
        await app.state.engine.dispose()

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert isinstance(body["details"], list)
    assert body["details"]
    for detail in body["details"]:
        assert set(detail) == {"loc", "msg", "type"}
    assert body["details"][0]["loc"] == ["body", "colour"]
    assert body["details"][0]["type"] == "extra_forbidden"


def test_integrity_error_codes() -> None:
    assert integrity_error_status_code("23505") == (409, "key_conflict")
    assert integrity_error_status_code("23503") == (409, "in_use")
    assert integrity_error_status_code("23514") == (422, "validation_error")
    assert integrity_error_status_code(None) == (500, "internal_error")


async def test_session_dependency(app: FastAPI) -> None:
    async with app.state.sessionmaker() as session:
        assert (await session.execute(text("SELECT 1"))).scalar() == 1

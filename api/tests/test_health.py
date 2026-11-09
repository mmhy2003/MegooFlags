from pathlib import Path

import httpx

from megooflags_api.main import create_app

VERSION = (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()


async def test_healthz_reports_ok_and_version() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": VERSION}

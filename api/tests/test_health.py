from pathlib import Path

from conftest import ClientFactory

VERSION = (Path(__file__).resolve().parents[2] / "VERSION").read_text(encoding="utf-8").strip()


async def test_healthz_reports_ok_and_version(client_for: ClientFactory) -> None:
    async with client_for(None) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": VERSION}

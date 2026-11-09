from fastapi import FastAPI

from megooflags_api import __version__
from megooflags_api.routers import health
from megooflags_api.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="MegooFlags API", version=__version__)
    app.state.settings = settings
    app.include_router(health.router)
    return app


app = create_app()

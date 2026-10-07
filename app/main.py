"""FastAPI application entrypoint.

Run locally with:  uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.files import router as files_router
from app.config import get_settings
from app.database import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=get_settings().app_name,
    version=__version__,
    description=(
        "Upload KML or zipped shapefiles and get geodesic area, perimeter and "
        "length for every feature."
    ),
    lifespan=lifespan,
)

app.include_router(files_router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}

"""Production entry point: the API under /api and the built React app at /.

    uvicorn app.server:app --host 0.0.0.0 --port 10000

Local development uses `app.main:app` with the Vite dev server instead.
"""
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .main import app as api

STATIC_DIR = Path(os.getenv("STATIC_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist"))

app = FastAPI(title="Spelunk", docs_url=None, redoc_url=None)
app.mount("/api", api)

if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")

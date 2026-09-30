"""
Blood Warriors — circle-first donor matching engine.
Section 6: "The backend is written in Python using FastAPI. The matching
algorithm, the reply-window timers and the WhatsApp webhook all run in this
one service."
"""
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from .database import Base, engine, SessionLocal
from .routers import cycles, activity, emergency, whatsapp, demo, patients
from . import outreach, models
from . import seed_data

scheduler = BackgroundScheduler(timezone="UTC")


def _run_timeout_sweep():
    db = SessionLocal()
    try:
        outreach.sweep_timeouts(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)

    # Auto-seed on first boot (or after a free-tier host wipes ephemeral
    # disk on restart) so a fresh deployment is always demo-ready without a
    # manual step. Set SKIP_AUTO_SEED=1 to disable.
    if not os.environ.get("SKIP_AUTO_SEED"):
        db = SessionLocal()
        try:
            has_data = db.query(models.Patient).first() is not None
        finally:
            db.close()
        if not has_data:
            seed_data.seed()

    # Sweep frequently in fast-demo mode, otherwise every couple of minutes.
    interval = 5 if os.environ.get("DEMO_REPLY_WINDOW_SECONDS") else 120
    scheduler.add_job(_run_timeout_sweep, "interval", seconds=interval, id="timeout_sweep")
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Blood Warriors — Donor Matching Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # PoC only — the mock UI is a local static file
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cycles.router)
app.include_router(activity.router)
app.include_router(emergency.router)
app.include_router(whatsapp.router)
app.include_router(demo.router)
app.include_router(patients.router)


@app.get("/", include_in_schema=False)
def root_redirect():
    # So the bare deployed URL takes a visitor straight to the demo.
    return RedirectResponse(url="/chat/")


@app.get("/api")
def root():
    return {
        "service": "Blood Warriors donor matching engine",
        "docs": "/docs",
        "chat_ui": "/chat/",
        "note": "The dashboard runs separately — see dashboard/streamlit_app.py.",
    }


# Serve the WhatsApp mock chat UI from this same service at /chat/, so
# there's one deployed URL to share instead of hosting it separately.
# (Mounted last so it doesn't shadow the API routes above.)
_static_dir = os.path.join(os.path.dirname(__file__), "..", "..", "whatsapp_mock")
if os.path.isdir(_static_dir):
    app.mount("/chat", StaticFiles(directory=_static_dir, html=True), name="chat")

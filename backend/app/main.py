import os
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.routes.emergency_routes import router as emergency_router
from app.routes.websocket_routes import router as websocket_router
from app.routes.contact_routes import router as contact_router
from app.config.database import engine, Base


def _preload_models():
    # Loading the speech and translation models takes a while; do it once in
    # the background at start-up instead of during the first call.
    from app.services import speech_service, translation_service
    try:
        speech_service.preload(live=True)
        translation_service.preload()
        speech_service.preload(live=False)
        print("All models loaded", flush=True)
    except Exception as error:
        print(f"Model preload failed (models will load on first use): {error!r}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting backend server...")
    if os.getenv("PRELOAD_MODELS", "1") == "1":
        threading.Thread(target=_preload_models, daemon=True).start()
    yield
    print("Shutting down backend server...")


app = FastAPI(
    title="Emergency NLP Platform",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

app.include_router(emergency_router)
app.include_router(websocket_router)
app.include_router(contact_router)


@app.get("/")
def home():
    return {
        "message": "Emergency NLP Backend Running"
    }
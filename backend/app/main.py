from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.routes.emergency_routes import router as emergency_router
from app.routes.websocket_routes import router as websocket_router
from app.routes.contact_routes import router as contact_router
from app.config.database import engine, Base


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting backend server...")
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
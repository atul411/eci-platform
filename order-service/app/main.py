import logging
import logging.config
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.database import Base, SessionLocal, engine
from app.routers import orders
import app.services.order_service as svc

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "formatter": "default",
            "level": "DEBUG",
        }
    },
    "root": {"handlers": ["stderr"], "level": "INFO"},
    "loggers": {
        "order": {"handlers": ["stderr"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"level": "WARNING"},
        "sqlalchemy.engine": {"level": "WARNING"},
    },
})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    svc.seed(SessionLocal)

    yield


app = FastAPI(
    title="Order Service",
    version="1.0.0",
    lifespan=lifespan,
    description="Orchestrates order placement, cancellation, and refunds.",
)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    cid = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.cid = cid
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = cid
    return response


@app.exception_handler(Exception)
async def http_exc(request: Request, exc: Exception):
    from fastapi import HTTPException
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": {
            "code": str(exc.status_code),
            "message": exc.detail,
            "correlationId": getattr(request.state, "cid", str(uuid.uuid4())),
        }})
    return JSONResponse(status_code=500, content={"error": {"code": "500", "message": "Internal server error"}})


@app.get("/health")
def health():
    return {"status": "healthy", "service": "order-service", "timestamp": datetime.utcnow().isoformat()}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(orders.router)

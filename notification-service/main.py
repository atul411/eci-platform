"""Notification Service — log and simulate email/SMS notifications."""
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pythonjsonlogger import jsonlogger
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
import logging

# ── Logging ──────────────────────────────────────────────────────────────────
_logger = logging.getLogger("notification")
_h = logging.StreamHandler()
_h.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
_logger.addHandler(_h)
_logger.setLevel(logging.INFO)


def _mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "***"
    u, d = email.split("@", 1)
    return u[:2] + "***@" + d


def _mask_phone(phone: str) -> str:
    if not phone:
        return "***"
    return re.sub(r"\d(?=\d{4})", "*", phone)


def log(msg: str, level: str = "info", **ctx):
    # Mask sensitive fields before logging
    for k in ("email", "recipient_email"):
        if k in ctx:
            ctx[k] = _mask_email(str(ctx[k]))
    for k in ("phone", "recipient_phone"):
        if k in ctx:
            ctx[k] = _mask_phone(str(ctx[k]))
    ctx["service"] = "notification-service"
    getattr(_logger, level)(msg, extra=ctx)


# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./notification.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class NotificationLog(Base):
    __tablename__ = "notifications_log"
    notification_id = Column(Integer, primary_key=True, index=True)
    notification_type = Column(String(50))        # ORDER_CONFIRMED | PAYMENT_FAILED | SHIPPED | DELIVERED | LOW_STOCK | etc.
    channel           = Column(String(20), default="EMAIL")   # EMAIL | SMS
    recipient_id      = Column(Integer)
    recipient_contact = Column(String(200))       # masked at retrieval
    message           = Column(Text)
    status            = Column(String(20), default="SENT")    # SENT | FAILED
    reference_id      = Column(String(100))       # order_id / payment_id etc.
    created_at        = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Metrics ───────────────────────────────────────────────────────────────────
NOTIFICATIONS_SENT   = Counter("notifications_sent_total",   "Notifications sent", ["type"])
NOTIFICATIONS_FAILED = Counter("notifications_failed_total", "Notifications failed")


# ── Schemas ───────────────────────────────────────────────────────────────────
class NotificationRequest(BaseModel):
    notification_type: str
    channel:           Optional[str] = "EMAIL"
    recipient_id:      Optional[int] = None
    recipient_contact: Optional[str] = None   # email or phone (will be masked in logs)
    message:           str
    reference_id:      Optional[str] = None


class NotificationOut(BaseModel):
    notification_id:   int
    notification_type: str
    channel:           str
    recipient_id:      Optional[int]
    message:           str
    status:            str
    reference_id:      Optional[str]
    created_at:        datetime

    class Config:
        from_attributes = True


class Paginated(BaseModel):
    data:  List[NotificationOut]
    total: int
    page:  int
    size:  int


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield


app = FastAPI(title="Notification Service", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    cid = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.cid = cid
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = cid
    return response


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "healthy", "service": "notification-service", "timestamp": datetime.utcnow().isoformat()}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/notifications", response_model=Paginated)
def list_notifications(
    page:              int           = Query(1, ge=1),
    size:              int           = Query(20, ge=1, le=100),
    notification_type: Optional[str] = None,
    recipient_id:      Optional[int] = None,
    db: Session = Depends(get_db),
):
    q = db.query(NotificationLog)
    if notification_type: q = q.filter(NotificationLog.notification_type == notification_type)
    if recipient_id:      q = q.filter(NotificationLog.recipient_id == recipient_id)
    total = q.count()
    items = q.order_by(NotificationLog.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return {"data": items, "total": total, "page": page, "size": size}


@app.post("/v1/notifications", response_model=NotificationOut, status_code=201)
def send_notification(body: NotificationRequest, db: Session = Depends(get_db)):
    # Simulate delivery (always succeeds in this implementation; plug in real provider here)
    n = NotificationLog(
        notification_type=body.notification_type,
        channel=body.channel or "EMAIL",
        recipient_id=body.recipient_id,
        recipient_contact=body.recipient_contact,
        message=body.message,
        status="SENT",
        reference_id=body.reference_id,
    )
    db.add(n)
    db.commit()
    db.refresh(n)

    NOTIFICATIONS_SENT.labels(type=body.notification_type).inc()
    log(
        "Notification sent",
        notification_id=n.notification_id,
        notification_type=body.notification_type,
        recipient_id=body.recipient_id,
        email=body.recipient_contact if body.channel == "EMAIL" else None,
        phone=body.recipient_contact if body.channel == "SMS" else None,
    )
    return n


@app.get("/v1/notifications/{notification_id}", response_model=NotificationOut)
def get_notification(notification_id: int, db: Session = Depends(get_db)):
    n = db.query(NotificationLog).filter(NotificationLog.notification_id == notification_id).first()
    if not n:
        from fastapi import HTTPException
        raise HTTPException(404, f"Notification {notification_id} not found")
    return n


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8006)))

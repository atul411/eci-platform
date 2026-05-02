"""Shipping Service — create shipments and manage delivery tracking."""
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pythonjsonlogger import jsonlogger
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
import logging

# ── Logging ──────────────────────────────────────────────────────────────────
_logger = logging.getLogger("shipping")
_h = logging.StreamHandler()
_h.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
_logger.addHandler(_h)
_logger.setLevel(logging.INFO)


def log(msg: str, level: str = "info", **ctx):
    ctx["service"] = "shipping-service"
    getattr(_logger, level)(msg, extra=ctx)


# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./shipping.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

CARRIERS = ["BlueDart", "Delhivery", "DTDC", "Ekart", "FedEx"]


class Shipment(Base):
    __tablename__ = "shipments"
    shipment_id  = Column(Integer, primary_key=True, index=True)
    order_id     = Column(Integer, index=True, nullable=False)
    carrier      = Column(String(100))
    # PENDING → PACKED → SHIPPED → DELIVERED | CANCELLED
    status       = Column(String(20), default="PENDING")
    tracking_no  = Column(String(100))
    shipped_at   = Column(DateTime)
    delivered_at = Column(DateTime)
    created_at   = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Metrics ───────────────────────────────────────────────────────────────────
SHIPMENTS_CREATED   = Counter("shipments_created_total",   "Shipments created")
SHIPMENTS_DELIVERED = Counter("shipments_delivered_total", "Shipments delivered")


# ── Schemas ───────────────────────────────────────────────────────────────────
class ShipmentCreate(BaseModel):
    order_id: int
    carrier:  Optional[str] = None


class StatusUpdate(BaseModel):
    # PACKED | SHIPPED | DELIVERED | CANCELLED
    status: str


class ShipmentOut(BaseModel):
    shipment_id:  int
    order_id:     int
    carrier:      Optional[str]
    status:       str
    tracking_no:  Optional[str]
    shipped_at:   Optional[datetime]
    delivered_at: Optional[datetime]
    created_at:   datetime

    class Config:
        from_attributes = True


class Paginated(BaseModel):
    data:  List[ShipmentOut]
    total: int
    page:  int
    size:  int


VALID_TRANSITIONS = {
    "PENDING":   {"PACKED", "CANCELLED"},
    "PACKED":    {"SHIPPED", "CANCELLED"},
    "SHIPPED":   {"DELIVERED", "CANCELLED"},
    "DELIVERED": set(),
    "CANCELLED": set(),
}


# ── Seed ──────────────────────────────────────────────────────────────────────
def seed():
    db = SessionLocal()
    try:
        if db.query(Shipment).count() > 0:
            return
        csv_path = os.getenv("SHIPMENTS_CSV", "/data/eci_shipments_indian.csv")
        if not os.path.exists(csv_path):
            return
        df = pd.read_csv(csv_path)
        for _, r in df.iterrows():
            shipped = pd.to_datetime(r["shipped_at"], errors="coerce")
            delivered = pd.to_datetime(r["delivered_at"], errors="coerce")
            db.merge(Shipment(
                shipment_id=int(r["shipment_id"]),
                order_id=int(r["order_id"]),
                carrier=str(r["carrier"]),
                status=str(r["status"]),
                tracking_no=str(r["tracking_no"]),
                shipped_at=shipped.to_pydatetime() if not pd.isna(shipped) else None,
                delivered_at=delivered.to_pydatetime() if not pd.isna(delivered) else None,
            ))
        db.commit()
        log("Shipments seeded", count=len(df))
    except Exception as exc:
        log(f"Seed error: {exc}", level="error")
    finally:
        db.close()


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed()
    yield


app = FastAPI(title="Shipping Service", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    cid = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.cid = cid
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = cid
    return response


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    return JSONResponse(exc.status_code, {"error": {
        "code": str(exc.status_code),
        "message": exc.detail,
        "correlationId": getattr(request.state, "cid", str(uuid.uuid4())),
    }})


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "healthy", "service": "shipping-service", "timestamp": datetime.utcnow().isoformat()}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/shipments", response_model=Paginated)
def list_shipments(
    page:     int           = Query(1, ge=1),
    size:     int           = Query(20, ge=1, le=100),
    order_id: Optional[int] = None,
    status:   Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Shipment)
    if order_id: q = q.filter(Shipment.order_id == order_id)
    if status:   q = q.filter(Shipment.status == status)
    total = q.count()
    items = q.order_by(Shipment.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return {"data": items, "total": total, "page": page, "size": size}


@app.post("/v1/shipments", response_model=ShipmentOut, status_code=201)
def create_shipment(body: ShipmentCreate, db: Session = Depends(get_db)):
    import random
    carrier     = body.carrier or random.choice(CARRIERS)
    tracking_no = f"TRK{uuid.uuid4().hex[:8].upper()}"
    s = Shipment(order_id=body.order_id, carrier=carrier, tracking_no=tracking_no, status="PENDING")
    db.add(s)
    db.commit()
    db.refresh(s)
    SHIPMENTS_CREATED.inc()
    log("Shipment created", shipment_id=s.shipment_id, order_id=body.order_id, tracking_no=tracking_no)
    return s


@app.get("/v1/shipments/{shipment_id}", response_model=ShipmentOut)
def get_shipment(shipment_id: int, db: Session = Depends(get_db)):
    s = db.query(Shipment).filter(Shipment.shipment_id == shipment_id).first()
    if not s:
        raise HTTPException(404, f"Shipment {shipment_id} not found")
    return s


@app.patch("/v1/shipments/{shipment_id}/status", response_model=ShipmentOut)
def update_status(shipment_id: int, body: StatusUpdate, db: Session = Depends(get_db)):
    s = db.query(Shipment).filter(Shipment.shipment_id == shipment_id).first()
    if not s:
        raise HTTPException(404, f"Shipment {shipment_id} not found")

    new_status = body.status.upper()
    allowed = VALID_TRANSITIONS.get(s.status, set())
    if new_status not in allowed:
        raise HTTPException(400, f"Cannot transition from '{s.status}' to '{new_status}'")

    s.status = new_status
    if new_status == "SHIPPED":
        s.shipped_at = datetime.utcnow()
    if new_status == "DELIVERED":
        s.delivered_at = datetime.utcnow()
        SHIPMENTS_DELIVERED.inc()

    db.commit()
    db.refresh(s)
    log("Shipment status updated", shipment_id=shipment_id, status=new_status)
    return s


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8005)))

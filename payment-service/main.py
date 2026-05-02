"""Payment Service — charge, capture, refund with idempotency."""
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
from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
import logging

# ── Logging ──────────────────────────────────────────────────────────────────
_logger = logging.getLogger("payment")
_h = logging.StreamHandler()
_h.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
_logger.addHandler(_h)
_logger.setLevel(logging.INFO)


def log(msg: str, level: str = "info", **ctx):
    ctx["service"] = "payment-service"
    getattr(_logger, level)(msg, extra=ctx)


# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./payment.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Payment(Base):
    __tablename__ = "payments"
    payment_id    = Column(Integer, primary_key=True, index=True)
    order_id      = Column(Integer, index=True, nullable=False)
    amount        = Column(Float, nullable=False)
    method        = Column(String(50), default="CARD")  # CARD | UPI | COD | NETBANKING
    status        = Column(String(20), default="PENDING")  # PENDING | SUCCESS | FAILED | REFUNDED
    reference     = Column(String(100))
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    key        = Column(String(200), primary_key=True)
    payment_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Metrics ───────────────────────────────────────────────────────────────────
PAYMENTS_CHARGED  = Counter("payments_charged_total",  "Successful charges")
PAYMENTS_FAILED   = Counter("payments_failed_total",   "Failed payment attempts")
PAYMENTS_REFUNDED = Counter("payments_refunded_total", "Refunds issued")


# ── Schemas ───────────────────────────────────────────────────────────────────
class ChargeRequest(BaseModel):
    order_id:        int
    amount:          float
    method:          Optional[str] = "CARD"
    idempotency_key: str


class RefundRequest(BaseModel):
    reason:          Optional[str] = None
    idempotency_key: str


class PaymentOut(BaseModel):
    payment_id: int
    order_id:   int
    amount:     float
    method:     str
    status:     str
    reference:  Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class Paginated(BaseModel):
    data:  List[PaymentOut]
    total: int
    page:  int
    size:  int


# ── Seed ──────────────────────────────────────────────────────────────────────
def seed():
    db = SessionLocal()
    try:
        if db.query(Payment).count() > 0:
            return
        csv_path = os.getenv("PAYMENTS_CSV", "/data/eci_payments_indian.csv")
        if not os.path.exists(csv_path):
            return
        df = pd.read_csv(csv_path)
        for _, r in df.iterrows():
            db.merge(Payment(
                payment_id=int(r["payment_id"]),
                order_id=int(r["order_id"]),
                amount=float(r["amount"]),
                method=str(r["method"]),
                status=str(r["status"]),
                reference=f"SEED-{r['payment_id']}",
                created_at=pd.to_datetime(r["created_at"]).to_pydatetime(),
            ))
        db.commit()
        log("Payments seeded", count=len(df))
    except Exception as exc:
        log(f"Seed error: {exc}", level="error")
    finally:
        db.close()


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed()
    yield


app = FastAPI(title="Payment Service", version="1.0.0", lifespan=lifespan)


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
    return {"status": "healthy", "service": "payment-service", "timestamp": datetime.utcnow().isoformat()}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/payments", response_model=Paginated)
def list_payments(
    page:     int           = Query(1, ge=1),
    size:     int           = Query(20, ge=1, le=100),
    order_id: Optional[int] = None,
    status:   Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Payment)
    if order_id: q = q.filter(Payment.order_id == order_id)
    if status:   q = q.filter(Payment.status == status)
    total = q.count()
    items = q.order_by(Payment.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return {"data": items, "total": total, "page": page, "size": size}


@app.post("/v1/payments/charge", response_model=PaymentOut, status_code=201)
def charge(body: ChargeRequest, db: Session = Depends(get_db)):
    # Idempotency check
    existing_key = db.query(IdempotencyKey).filter(IdempotencyKey.key == body.idempotency_key).first()
    if existing_key:
        p = db.query(Payment).filter(Payment.payment_id == existing_key.payment_id).first()
        log("Idempotent charge hit", idempotency_key=body.idempotency_key)
        return p

    if body.amount <= 0:
        PAYMENTS_FAILED.inc()
        raise HTTPException(400, "Amount must be positive")

    # Simulate payment gateway (90% success rate for CARD/UPI/NETBANKING, 100% for COD)
    method = (body.method or "CARD").upper()
    import random
    success = method == "COD" or random.random() < 0.90

    status    = "SUCCESS" if success else "FAILED"
    reference = f"REF-{uuid.uuid4().hex[:12].upper()}" if success else None

    p = Payment(
        order_id=body.order_id,
        amount=body.amount,
        method=method,
        status=status,
        reference=reference,
    )
    db.add(p)
    db.flush()

    # Save idempotency key
    db.add(IdempotencyKey(key=body.idempotency_key, payment_id=p.payment_id))
    db.commit()
    db.refresh(p)

    if success:
        PAYMENTS_CHARGED.inc()
        log("Payment charged", payment_id=p.payment_id, order_id=body.order_id, amount=body.amount)
    else:
        PAYMENTS_FAILED.inc()
        log("Payment failed", payment_id=p.payment_id, order_id=body.order_id, level="warning")
        raise HTTPException(402, f"Payment declined by gateway (payment_id={p.payment_id})")

    return p


@app.get("/v1/payments/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    p = db.query(Payment).filter(Payment.payment_id == payment_id).first()
    if not p:
        raise HTTPException(404, f"Payment {payment_id} not found")
    return p


@app.post("/v1/payments/{payment_id}/refund", response_model=PaymentOut)
def refund(payment_id: int, body: RefundRequest, db: Session = Depends(get_db)):
    # Idempotency
    existing_key = db.query(IdempotencyKey).filter(IdempotencyKey.key == body.idempotency_key).first()
    if existing_key:
        p = db.query(Payment).filter(Payment.payment_id == existing_key.payment_id).first()
        return p

    p = db.query(Payment).filter(Payment.payment_id == payment_id).first()
    if not p:
        raise HTTPException(404, f"Payment {payment_id} not found")
    if p.status != "SUCCESS":
        raise HTTPException(400, f"Cannot refund payment in status '{p.status}'")

    p.status     = "REFUNDED"
    p.updated_at = datetime.utcnow()

    db.add(IdempotencyKey(key=body.idempotency_key, payment_id=p.payment_id))
    db.commit()
    db.refresh(p)

    PAYMENTS_REFUNDED.inc()
    log("Payment refunded", payment_id=payment_id, reason=body.reason)
    return p


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8004)))

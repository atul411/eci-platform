import logging
import os
import random
import uuid
from datetime import datetime

import pandas as pd
from fastapi import HTTPException
from prometheus_client import Counter
from sqlalchemy.orm import Session

from app.config import settings
from app.models import IdempotencyKey, Payment
from app.schemas import ChargeRequest, RefundRequest

logger = logging.getLogger("payment")

PAYMENTS_CHARGED  = Counter("payments_charged_total",  "Successful charges")
PAYMENTS_FAILED   = Counter("payments_failed_total",   "Failed payment attempts")
PAYMENTS_REFUNDED = Counter("payments_refunded_total", "Refunds issued")


def seed(db_factory):
    db = db_factory()
    try:
        if db.query(Payment).count() > 0:
            return
        if not os.path.exists(settings.PAYMENTS_CSV):
            return
        df = pd.read_csv(settings.PAYMENTS_CSV)
        for _, r in df.iterrows():
            db.merge(Payment(
                payment_id=int(r["payment_id"]), order_id=int(r["order_id"]),
                amount=float(r["amount"]), method=str(r["method"]),
                status=str(r["status"]), reference=f"SEED-{r['payment_id']}",
                created_at=pd.to_datetime(r["created_at"]).to_pydatetime(),
            ))
        db.commit()
        logger.info("Seeded %d payments", len(df))
    except Exception as exc:
        logger.error("Seed error: %s", exc)
    finally:
        db.close()


def list_payments(db: Session, page: int, size: int,
                  order_id=None, status=None) -> dict:
    q = db.query(Payment)
    if order_id: q = q.filter(Payment.order_id == order_id)
    if status:   q = q.filter(Payment.status == status)
    total = q.count()
    items = q.order_by(Payment.created_at.desc()).offset((page-1)*size).limit(size).all()
    return {"data": items, "total": total, "page": page, "size": size}


def get_payment(db: Session, payment_id: int) -> Payment:
    p = db.query(Payment).filter(Payment.payment_id == payment_id).first()
    if not p:
        raise HTTPException(404, f"Payment {payment_id} not found")
    return p


def charge(db: Session, body: ChargeRequest) -> Payment:
    # Idempotency check
    key = db.query(IdempotencyKey).filter(IdempotencyKey.key == body.idempotency_key).first()
    if key:
        logger.info("Idempotent charge hit: key=%s", body.idempotency_key)
        return get_payment(db, key.payment_id)

    if body.amount <= 0:
        PAYMENTS_FAILED.inc()
        raise HTTPException(400, "Amount must be positive")

    method  = (body.method or "CARD").upper()
    success = method == "COD" or random.random() < 0.90
    status  = "SUCCESS" if success else "FAILED"
    ref     = f"REF-{uuid.uuid4().hex[:12].upper()}" if success else None

    p = Payment(order_id=body.order_id, amount=body.amount,
                method=method, status=status, reference=ref)
    db.add(p)
    db.flush()
    db.add(IdempotencyKey(key=body.idempotency_key, payment_id=p.payment_id))
    db.commit()
    db.refresh(p)

    if success:
        PAYMENTS_CHARGED.inc()
        logger.info("Payment charged: id=%d order_id=%d amount=%.2f",
                    p.payment_id, body.order_id, body.amount)
    else:
        PAYMENTS_FAILED.inc()
        logger.warning("Payment failed: id=%d order_id=%d", p.payment_id, body.order_id)
        raise HTTPException(402, f"Payment declined by gateway (payment_id={p.payment_id})")
    return p


def refund(db: Session, payment_id: int, body: RefundRequest) -> Payment:
    key = db.query(IdempotencyKey).filter(IdempotencyKey.key == body.idempotency_key).first()
    if key:
        return get_payment(db, key.payment_id)

    p = get_payment(db, payment_id)
    if p.status != "SUCCESS":
        raise HTTPException(400, f"Cannot refund payment in status '{p.status}'")

    p.status     = "REFUNDED"
    p.updated_at = datetime.utcnow()
    db.add(IdempotencyKey(key=body.idempotency_key, payment_id=p.payment_id))
    db.commit()
    db.refresh(p)
    PAYMENTS_REFUNDED.inc()
    logger.info("Payment refunded: id=%d reason=%s", payment_id, body.reason)
    return p

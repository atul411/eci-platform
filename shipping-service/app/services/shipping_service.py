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
from app.models import Shipment
from app.schemas import ShipmentCreate, StatusUpdate

logger = logging.getLogger("shipping")

SHIPMENTS_CREATED   = Counter("shipments_created_total",   "Shipments created")
SHIPMENTS_DELIVERED = Counter("shipments_delivered_total", "Shipments delivered")

CARRIERS = ["BlueDart", "Delhivery", "DTDC", "Ekart", "FedEx"]

VALID_TRANSITIONS = {
    "PENDING":   {"PACKED", "CANCELLED"},
    "PACKED":    {"SHIPPED", "CANCELLED"},
    "SHIPPED":   {"DELIVERED", "CANCELLED"},
    "DELIVERED": set(),
    "CANCELLED": set(),
}


def seed(db_factory):
    db = db_factory()
    try:
        if db.query(Shipment).count() > 0:
            return
        if not os.path.exists(settings.SHIPMENTS_CSV):
            return
        df = pd.read_csv(settings.SHIPMENTS_CSV)
        for _, r in df.iterrows():
            shipped   = pd.to_datetime(r["shipped_at"],   errors="coerce")
            delivered = pd.to_datetime(r["delivered_at"], errors="coerce")
            db.merge(Shipment(
                shipment_id=int(r["shipment_id"]), order_id=int(r["order_id"]),
                carrier=str(r["carrier"]), status=str(r["status"]),
                tracking_no=str(r["tracking_no"]),
                shipped_at=shipped.to_pydatetime()     if not pd.isna(shipped)   else None,
                delivered_at=delivered.to_pydatetime() if not pd.isna(delivered) else None,
            ))
        db.commit()
        logger.info("Seeded %d shipments", len(df))
    except Exception as exc:
        logger.error("Seed error: %s", exc)
    finally:
        db.close()


def list_shipments(db: Session, page: int, size: int,
                   order_id=None, status=None) -> dict:
    q = db.query(Shipment)
    if order_id: q = q.filter(Shipment.order_id == order_id)
    if status:   q = q.filter(Shipment.status == status)
    total = q.count()
    items = q.order_by(Shipment.created_at.desc()).offset((page-1)*size).limit(size).all()
    return {"data": items, "total": total, "page": page, "size": size}


def get_shipment(db: Session, shipment_id: int) -> Shipment:
    s = db.query(Shipment).filter(Shipment.shipment_id == shipment_id).first()
    if not s:
        raise HTTPException(404, f"Shipment {shipment_id} not found")
    return s


def create_shipment(db: Session, body: ShipmentCreate) -> Shipment:
    s = Shipment(
        order_id=body.order_id,
        carrier=body.carrier or random.choice(CARRIERS),
        tracking_no=f"TRK{uuid.uuid4().hex[:8].upper()}",
        status="PENDING",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    SHIPMENTS_CREATED.inc()
    logger.info("Shipment created: id=%d order_id=%d tracking=%s",
                s.shipment_id, body.order_id, s.tracking_no)
    return s


def update_status(db: Session, shipment_id: int, body: StatusUpdate) -> Shipment:
    s = get_shipment(db, shipment_id)
    new_status = body.status.upper()
    allowed    = VALID_TRANSITIONS.get(s.status, set())
    if new_status not in allowed:
        raise HTTPException(400, f"Cannot transition '{s.status}' → '{new_status}'")
    s.status = new_status
    if new_status == "SHIPPED":
        s.shipped_at   = datetime.utcnow()
    if new_status == "DELIVERED":
        s.delivered_at = datetime.utcnow()
        SHIPMENTS_DELIVERED.inc()
    db.commit()
    db.refresh(s)
    logger.info("Shipment %d status → %s", shipment_id, new_status)
    return s

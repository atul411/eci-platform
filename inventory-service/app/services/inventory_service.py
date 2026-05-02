import logging
import os
from datetime import datetime
from typing import List, Optional

import httpx
import pandas as pd
from fastapi import HTTPException
from prometheus_client import Counter, Histogram
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Inventory, InventoryMovement, Reservation

logger = logging.getLogger("inventory")

RESERVE_LATENCY   = Histogram("inventory_reserve_latency_ms",    "Reserve latency in ms")
RESERVATIONS_MADE = Counter("inventory_reservations_total",      "Reservations created")
RELEASES_MADE     = Counter("inventory_releases_total",          "Reservations released")
SHIPMENTS_MADE    = Counter("inventory_shipments_total",         "Inventory shipped")
STOCKOUTS_TOTAL   = Counter("stockouts_total",                   "Stock-out events")
EXPIRY_RELEASES   = Counter("inventory_expiry_releases_total",   "Reservations released by TTL reaper")


# ── Seed ──────────────────────────────────────────────────────────────────────
def seed(db_factory):
    db = db_factory()
    try:
        if db.query(Inventory).count() > 0:
            return
        if not os.path.exists(settings.INVENTORY_CSV):
            return
        df = pd.read_csv(settings.INVENTORY_CSV)
        for _, r in df.iterrows():
            db.merge(Inventory(
                inventory_id=int(r["inventory_id"]),
                product_id=int(r["product_id"]),
                warehouse=str(r["warehouse"]),
                on_hand=int(r["on_hand"]),
                reserved=0,
                updated_at=pd.to_datetime(r["updated_at"]).to_pydatetime(),
            ))
        db.commit()
        logger.info("Seeded %d inventory rows", len(df))
    except Exception as exc:
        logger.error("Seed error: %s", exc)
    finally:
        db.close()


# ── Helpers ───────────────────────────────────────────────────────────────────
def to_out(inv: Inventory) -> dict:
    return {
        "inventory_id": inv.inventory_id,
        "product_id":   inv.product_id,
        "warehouse":    inv.warehouse,
        "on_hand":      inv.on_hand,
        "reserved":     inv.reserved,
        "available":    inv.on_hand - inv.reserved,
        "updated_at":   inv.updated_at,
    }


def check_low_stock(product_ids: List[int], db: Session):
    for pid in set(product_ids):
        rows = db.query(Inventory).filter(Inventory.product_id == pid).all()
        total_available = sum(max(0, r.on_hand - r.reserved) for r in rows)
        if total_available < settings.LOW_STOCK_THRESHOLD:
            STOCKOUTS_TOTAL.inc()
            logger.warning("Low stock: product_id=%d available=%d", pid, total_available)
            try:
                httpx.post(
                    f"{settings.NOTIFICATION_URL}/v1/notifications",
                    json={
                        "notification_type": "LOW_STOCK",
                        "message": f"Product {pid} has only {total_available} units available.",
                        "reference_id": str(pid),
                    },
                    timeout=3,
                )
            except Exception:
                pass


def rollback_partial(allocations: list, db: Session):
    for alloc in allocations:
        inv = db.query(Inventory).filter(
            Inventory.product_id == alloc["product_id"],
            Inventory.warehouse  == alloc["warehouse"],
        ).first()
        if inv:
            inv.reserved   = max(0, inv.reserved - alloc["quantity"])
            inv.updated_at = datetime.utcnow()
    db.commit()


def do_release(reservation_id: str, order_id: int, db: Session) -> int:
    reservations = db.query(Reservation).filter(
        Reservation.reservation_id == reservation_id,
        Reservation.is_released    == False,
        Reservation.is_shipped     == False,
    ).all()
    if not reservations:
        return 0
    for res in reservations:
        inv = db.query(Inventory).filter(
            Inventory.product_id == res.product_id,
            Inventory.warehouse  == res.warehouse,
        ).first()
        if inv:
            inv.reserved   = max(0, inv.reserved - res.quantity)
            inv.updated_at = datetime.utcnow()
        res.is_released = True
        db.add(InventoryMovement(
            product_id=res.product_id, warehouse=res.warehouse,
            order_id=order_id, type="RELEASE", quantity=res.quantity,
        ))
    db.commit()
    RELEASES_MADE.inc(len(reservations))
    logger.info("Released %d reservations for reservation_id=%s", len(reservations), reservation_id)
    return len(reservations)


# ── Business operations ────────────────────────────────────────────────────────
def list_inventory(db: Session, page: int, size: int,
                   product_id=None, warehouse=None, low_stock=False) -> dict:
    from datetime import timedelta
    q = db.query(Inventory)
    if product_id: q = q.filter(Inventory.product_id == product_id)
    if warehouse:  q = q.filter(Inventory.warehouse == warehouse)
    if low_stock:
        rows = [r for r in q.all() if (r.on_hand - r.reserved) < settings.LOW_STOCK_THRESHOLD]
        return {"data": [to_out(r) for r in rows[((page-1)*size):(page*size)]],
                "total": len(rows), "page": page, "size": size}
    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return {"data": [to_out(r) for r in items], "total": total, "page": page, "size": size}


def get_stock_for_product(db: Session, product_id: int) -> dict:
    rows = db.query(Inventory).filter(Inventory.product_id == product_id).all()
    if not rows:
        raise HTTPException(404, f"No inventory for product {product_id}")
    return {
        "product_id":      product_id,
        "total_available": sum(max(0, r.on_hand - r.reserved) for r in rows),
        "warehouses":      [to_out(r) for r in rows],
    }


def reserve(db: Session, reservation_id: str, order_id: int, items: list,
            allow_backorder: bool, expires_at) -> dict:
    import time
    start = time.time()

    # Idempotency
    existing = db.query(Reservation).filter(
        Reservation.reservation_id == reservation_id,
        Reservation.is_released    == False,
    ).first()
    if existing:
        return {"success": True, "reservation_id": reservation_id, "message": "Already reserved (idempotent)"}

    allocations = []
    for item in items:
        if item.quantity <= 0:
            raise HTTPException(400, f"Invalid quantity {item.quantity} for product {item.product_id}")

        stocks = (db.query(Inventory)
                  .filter(Inventory.product_id == item.product_id)
                  .order_by((Inventory.on_hand - Inventory.reserved).desc())
                  .all())
        stocks = [s for s in stocks if (s.on_hand - s.reserved) > 0]

        if not stocks:
            rollback_partial(allocations, db)
            STOCKOUTS_TOTAL.inc()
            raise HTTPException(409, f"Product {item.product_id} is out of stock")

        single = next((s for s in stocks if (s.on_hand - s.reserved) >= item.quantity), None)
        if single:
            chosen = [{"inv": single, "qty": item.quantity}]
        else:
            remaining, chosen = item.quantity, []
            for s in stocks:
                take = min(s.on_hand - s.reserved, remaining)
                if take > 0:
                    chosen.append({"inv": s, "qty": take})
                    remaining -= take
                if remaining == 0:
                    break
            if remaining > 0:
                rollback_partial(allocations, db)
                if not allow_backorder:
                    STOCKOUTS_TOTAL.inc()
                    raise HTTPException(409,
                        f"Insufficient stock for product {item.product_id}: "
                        f"need {item.quantity}, available {item.quantity - remaining}")

        for c in chosen:
            fresh = db.query(Inventory).filter(
                Inventory.inventory_id == c["inv"].inventory_id
            ).with_for_update().first()
            if (fresh.on_hand - fresh.reserved) < c["qty"]:
                rollback_partial(allocations, db)
                raise HTTPException(409, f"Race condition: stock changed for product {item.product_id}")
            fresh.reserved  += c["qty"]
            fresh.updated_at = datetime.utcnow()
            db.add(Reservation(
                reservation_id=reservation_id, product_id=item.product_id,
                warehouse=c["inv"].warehouse, quantity=c["qty"],
                order_id=order_id, expires_at=expires_at,
            ))
            db.add(InventoryMovement(
                product_id=item.product_id, warehouse=c["inv"].warehouse,
                order_id=order_id, type="RESERVE", quantity=c["qty"],
            ))
            allocations.append({"product_id": item.product_id, "warehouse": c["inv"].warehouse, "quantity": c["qty"]})

    db.commit()
    RESERVE_LATENCY.observe((time.time() - start) * 1000)
    RESERVATIONS_MADE.inc()
    logger.info("Reserved reservation_id=%s order_id=%d", reservation_id, order_id)
    check_low_stock([a["product_id"] for a in allocations], db)
    return {"success": True, "reservation_id": reservation_id,
            "allocations": allocations, "expires_at": expires_at.isoformat()}


def ship_order(db: Session, order_id: int) -> dict:
    reservations = db.query(Reservation).filter(
        Reservation.order_id    == order_id,
        Reservation.is_released == False,
        Reservation.is_shipped  == False,
    ).all()
    if not reservations:
        raise HTTPException(404, f"No active reservations for order {order_id}")
    for res in reservations:
        inv = db.query(Inventory).filter(
            Inventory.product_id == res.product_id,
            Inventory.warehouse  == res.warehouse,
        ).with_for_update().first()
        if inv:
            inv.on_hand    = max(0, inv.on_hand  - res.quantity)
            inv.reserved   = max(0, inv.reserved - res.quantity)
            inv.updated_at = datetime.utcnow()
        res.is_shipped = True
        db.add(InventoryMovement(
            product_id=res.product_id, warehouse=res.warehouse,
            order_id=order_id, type="SHIP", quantity=res.quantity,
        ))
    db.commit()
    SHIPMENTS_MADE.inc()
    logger.info("Shipped %d reservation rows for order_id=%d", len(reservations), order_id)
    return {"success": True, "shipped_items": len(reservations)}


def restock(db: Session, product_id: int, warehouse: str, quantity: int) -> dict:
    inv = db.query(Inventory).filter(
        Inventory.product_id == product_id,
        Inventory.warehouse  == warehouse,
    ).first()
    if not inv:
        inv = Inventory(product_id=product_id, warehouse=warehouse, on_hand=0, reserved=0)
        db.add(inv)
    inv.on_hand   += quantity
    inv.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(inv)
    logger.info("Restocked product_id=%d warehouse=%s qty=%d", product_id, warehouse, quantity)
    return to_out(inv)


def list_movements(db: Session, page: int, size: int,
                   product_id=None, order_id=None, type_=None) -> dict:
    q = db.query(InventoryMovement)
    if product_id: q = q.filter(InventoryMovement.product_id == product_id)
    if order_id:   q = q.filter(InventoryMovement.order_id == order_id)
    if type_:      q = q.filter(InventoryMovement.type == type_)
    total = q.count()
    items = q.order_by(InventoryMovement.created_at.desc()).offset((page-1)*size).limit(size).all()
    return {
        "data": [{"movement_id": m.movement_id, "product_id": m.product_id,
                  "warehouse": m.warehouse, "order_id": m.order_id,
                  "type": m.type, "quantity": m.quantity, "created_at": m.created_at}
                 for m in items],
        "total": total, "page": page, "size": size,
    }

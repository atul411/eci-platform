"""Inventory Service — stock levels, atomic reservations, TTL reaper, low-stock alerts."""
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pythonjsonlogger import jsonlogger
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, Integer, String, create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker
import logging

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL          = os.getenv("DATABASE_URL", "sqlite:///./inventory.db")
NOTIFICATION_URL      = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8006")
LOW_STOCK_THRESHOLD   = int(os.getenv("LOW_STOCK_THRESHOLD", "10"))
RESERVATION_TTL_MINS  = int(os.getenv("RESERVATION_TTL_MINUTES", "15"))

# ── Logging ──────────────────────────────────────────────────────────────────
_logger = logging.getLogger("inventory")
_h = logging.StreamHandler()
_h.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
_logger.addHandler(_h)
_logger.setLevel(logging.INFO)


def log(msg: str, level: str = "info", **ctx):
    ctx["service"] = "inventory-service"
    getattr(_logger, level)(msg, extra=ctx)


# ── Database ──────────────────────────────────────────────────────────────────
engine      = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()


class Inventory(Base):
    __tablename__ = "inventory"
    inventory_id = Column(Integer, primary_key=True, index=True)
    product_id   = Column(Integer, index=True, nullable=False)
    warehouse    = Column(String(50), nullable=False)
    on_hand      = Column(Integer, default=0)
    reserved     = Column(Integer, default=0)
    updated_at   = Column(DateTime, default=datetime.utcnow)


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    movement_id = Column(Integer, primary_key=True, index=True)
    product_id  = Column(Integer, index=True)
    warehouse   = Column(String(50))
    order_id    = Column(Integer, index=True)
    type        = Column(String(20))    # RESERVE | RELEASE | SHIP
    quantity    = Column(Integer)
    created_at  = Column(DateTime, default=datetime.utcnow)


class Reservation(Base):
    """Tracks active reservations for TTL expiry management."""
    __tablename__ = "reservations"
    id             = Column(Integer, primary_key=True)
    reservation_id = Column(String(200), index=True)
    product_id     = Column(Integer)
    warehouse      = Column(String(50))
    quantity       = Column(Integer)
    order_id       = Column(Integer, index=True)
    expires_at     = Column(DateTime)
    is_released    = Column(Boolean, default=False)
    is_shipped     = Column(Boolean, default=False)
    created_at     = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Metrics ───────────────────────────────────────────────────────────────────
RESERVE_LATENCY  = Histogram("inventory_reserve_latency_ms", "Reserve latency in ms")
RESERVATIONS_MADE = Counter("inventory_reservations_total", "Reservations created")
RELEASES_MADE     = Counter("inventory_releases_total",     "Reservations released")
SHIPMENTS_MADE    = Counter("inventory_shipments_total",    "Inventory shipped")
STOCKOUTS_TOTAL   = Counter("stockouts_total",              "Stock-out events")
EXPIRY_RELEASES   = Counter("inventory_expiry_releases_total", "Reservations released by TTL reaper")


# ── Schemas ───────────────────────────────────────────────────────────────────
class ReserveItem(BaseModel):
    product_id: int
    sku:        Optional[str] = None
    quantity:   int


class ReserveRequest(BaseModel):
    reservation_id:  str                      # idempotency key
    order_id:        int
    items:           List[ReserveItem]
    allow_backorder: bool = False             # reject if stock insufficient


class ReleaseRequest(BaseModel):
    reservation_id: str
    order_id:       int


class ShipRequest(BaseModel):
    order_id: int


class RestockRequest(BaseModel):
    product_id: int
    warehouse:  str
    quantity:   int


class InventoryOut(BaseModel):
    inventory_id: int
    product_id:   int
    warehouse:    str
    on_hand:      int
    reserved:     int
    available:    int
    updated_at:   datetime

    class Config:
        from_attributes = True


class MovementOut(BaseModel):
    movement_id: int
    product_id:  int
    warehouse:   str
    order_id:    Optional[int]
    type:        str
    quantity:    int
    created_at:  datetime

    class Config:
        from_attributes = True


class Paginated(BaseModel):
    data:  list
    total: int
    page:  int
    size:  int


# ── Helpers ───────────────────────────────────────────────────────────────────
def _inv_to_out(inv: Inventory) -> dict:
    return {
        "inventory_id": inv.inventory_id,
        "product_id":   inv.product_id,
        "warehouse":    inv.warehouse,
        "on_hand":      inv.on_hand,
        "reserved":     inv.reserved,
        "available":    inv.on_hand - inv.reserved,
        "updated_at":   inv.updated_at,
    }


def _check_low_stock(product_ids: List[int], db: Session):
    """Fire low-stock alert for any product whose available stock is below threshold."""
    for pid in set(product_ids):
        rows = db.query(Inventory).filter(Inventory.product_id == pid).all()
        total_available = sum(max(0, r.on_hand - r.reserved) for r in rows)
        if total_available < LOW_STOCK_THRESHOLD:
            STOCKOUTS_TOTAL.inc()
            log("Low stock alert", product_id=pid, available=total_available, level="warning")
            try:
                httpx.post(
                    f"{NOTIFICATION_URL}/v1/notifications",
                    json={
                        "notification_type": "LOW_STOCK",
                        "message": f"Product {pid} has only {total_available} units available across all warehouses.",
                        "reference_id": str(pid),
                    },
                    timeout=3,
                )
            except Exception:
                pass  # notification failure must not block inventory operations


def _do_release(reservation_id: str, order_id: int, db: Session) -> int:
    """Release reservations; returns number of rows released."""
    reservations = db.query(Reservation).filter(
        Reservation.reservation_id == reservation_id,
        Reservation.is_released == False,
        Reservation.is_shipped  == False,
    ).all()

    if not reservations:
        return 0

    released_products = []
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
            product_id=res.product_id,
            warehouse=res.warehouse,
            order_id=order_id,
            type="RELEASE",
            quantity=res.quantity,
        ))
        released_products.append(res.product_id)

    db.commit()
    RELEASES_MADE.inc(len(reservations))
    log("Reservations released", reservation_id=reservation_id, count=len(reservations))
    return len(reservations)


# ── Background reaper ─────────────────────────────────────────────────────────
def _reaper_job():
    """Release expired reservations (TTL enforcement)."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        expired = db.query(Reservation).filter(
            Reservation.expires_at  <= now,
            Reservation.is_released == False,
            Reservation.is_shipped  == False,
        ).all()

        if not expired:
            return

        for res in expired:
            inv = db.query(Inventory).filter(
                Inventory.product_id == res.product_id,
                Inventory.warehouse  == res.warehouse,
            ).first()
            if inv:
                inv.reserved   = max(0, inv.reserved - res.quantity)
                inv.updated_at = now
            res.is_released = True
            db.add(InventoryMovement(
                product_id=res.product_id,
                warehouse=res.warehouse,
                order_id=res.order_id,
                type="RELEASE",
                quantity=res.quantity,
            ))

        db.commit()
        EXPIRY_RELEASES.inc(len(expired))
        log(f"TTL reaper released {len(expired)} expired reservation(s)")
    except Exception as exc:
        log(f"Reaper error: {exc}", level="error")
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(_reaper_job, "interval", minutes=1, id="reaper")


# ── Seed ──────────────────────────────────────────────────────────────────────
def seed():
    db = SessionLocal()
    try:
        if db.query(Inventory).count() > 0:
            return
        inv_csv = os.getenv("INVENTORY_CSV", "/data/eci_inventory_indian.csv")
        if os.path.exists(inv_csv):
            df = pd.read_csv(inv_csv)
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
            log("Inventory seeded", count=len(df))
    except Exception as exc:
        log(f"Seed error: {exc}", level="error")
    finally:
        db.close()


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed()
    scheduler.start()
    log("TTL reaper scheduler started")
    yield
    scheduler.shutdown()


app = FastAPI(title="Inventory Service", version="1.0.0", lifespan=lifespan)


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
    return {"status": "healthy", "service": "inventory-service", "timestamp": datetime.utcnow().isoformat()}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/inventory", response_model=Paginated)
def list_inventory(
    page:       int           = Query(1, ge=1),
    size:       int           = Query(20, ge=1, le=100),
    product_id: Optional[int] = None,
    warehouse:  Optional[str] = None,
    low_stock:  bool          = False,
    db: Session = Depends(get_db),
):
    q = db.query(Inventory)
    if product_id: q = q.filter(Inventory.product_id == product_id)
    if warehouse:  q = q.filter(Inventory.warehouse == warehouse)
    if low_stock:
        # Filter where available < threshold — done in Python since SQLite lacks direct expressions
        all_rows = q.all()
        rows = [r for r in all_rows if (r.on_hand - r.reserved) < LOW_STOCK_THRESHOLD]
        return {"data": [_inv_to_out(r) for r in rows[((page-1)*size):(page*size)]],
                "total": len(rows), "page": page, "size": size}
    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return {"data": [_inv_to_out(r) for r in items], "total": total, "page": page, "size": size}


@app.get("/v1/inventory/product/{product_id}")
def get_stock_for_product(product_id: int, db: Session = Depends(get_db)):
    rows = db.query(Inventory).filter(Inventory.product_id == product_id).all()
    if not rows:
        raise HTTPException(404, f"No inventory found for product {product_id}")
    total_available = sum(max(0, r.on_hand - r.reserved) for r in rows)
    return {
        "product_id":      product_id,
        "total_available": total_available,
        "warehouses":      [_inv_to_out(r) for r in rows],
    }


@app.post("/v1/inventory/reserve")
def reserve(body: ReserveRequest, db: Session = Depends(get_db)):
    import time
    start = time.time()

    # ── Idempotency: if already reserved and not released, return OK ──────────
    existing = db.query(Reservation).filter(
        Reservation.reservation_id == body.reservation_id,
        Reservation.is_released    == False,
    ).first()
    if existing:
        log("Idempotent reserve hit", reservation_id=body.reservation_id)
        return {"success": True, "reservation_id": body.reservation_id, "message": "Already reserved (idempotent)"}

    expires_at = datetime.utcnow() + timedelta(minutes=RESERVATION_TTL_MINS)
    # Tracks all allocations so we can roll back on partial failure
    allocations: List[Dict] = []

    for item in body.items:
        if item.quantity <= 0:
            raise HTTPException(400, f"Invalid quantity {item.quantity} for product {item.product_id}")

        # Get all warehouses with available stock, best-first
        stocks = (
            db.query(Inventory)
            .filter(Inventory.product_id == item.product_id)
            .order_by((Inventory.on_hand - Inventory.reserved).desc())
            .all()
        )
        stocks = [s for s in stocks if (s.on_hand - s.reserved) > 0]

        if not stocks:
            _rollback_partial(allocations, db)
            STOCKOUTS_TOTAL.inc()
            raise HTTPException(409, f"Product {item.product_id} is out of stock")

        # Prefer single-warehouse fulfillment
        single = next((s for s in stocks if (s.on_hand - s.reserved) >= item.quantity), None)
        if single:
            chosen = [{"inv": single, "qty": item.quantity}]
        else:
            # Split across warehouses
            remaining = item.quantity
            chosen = []
            for s in stocks:
                avail = s.on_hand - s.reserved
                take  = min(avail, remaining)
                if take > 0:
                    chosen.append({"inv": s, "qty": take})
                    remaining -= take
                if remaining == 0:
                    break
            if remaining > 0:
                _rollback_partial(allocations, db)
                if not body.allow_backorder:
                    STOCKOUTS_TOTAL.inc()
                    raise HTTPException(
                        409,
                        f"Insufficient stock for product {item.product_id}: "
                        f"need {item.quantity}, available {item.quantity - remaining}",
                    )

        for c in chosen:
            inv = c["inv"]
            qty = c["qty"]
            # Re-verify under lock to prevent race conditions
            fresh = db.query(Inventory).filter(Inventory.inventory_id == inv.inventory_id).with_for_update().first()
            if (fresh.on_hand - fresh.reserved) < qty:
                _rollback_partial(allocations, db)
                raise HTTPException(409, f"Race condition: stock changed for product {item.product_id}")

            fresh.reserved   += qty
            fresh.updated_at  = datetime.utcnow()

            res = Reservation(
                reservation_id=body.reservation_id,
                product_id=item.product_id,
                warehouse=inv.warehouse,
                quantity=qty,
                order_id=body.order_id,
                expires_at=expires_at,
            )
            db.add(res)
            db.add(InventoryMovement(
                product_id=item.product_id,
                warehouse=inv.warehouse,
                order_id=body.order_id,
                type="RESERVE",
                quantity=qty,
            ))
            allocations.append({"product_id": item.product_id, "warehouse": inv.warehouse, "quantity": qty})

    db.commit()

    elapsed_ms = (time.time() - start) * 1000
    RESERVE_LATENCY.observe(elapsed_ms)
    RESERVATIONS_MADE.inc()
    log("Inventory reserved", reservation_id=body.reservation_id, order_id=body.order_id)

    _check_low_stock([a["product_id"] for a in allocations], db)

    return {
        "success":        True,
        "reservation_id": body.reservation_id,
        "allocations":    allocations,
        "expires_at":     expires_at.isoformat(),
    }


def _rollback_partial(allocations: List[Dict], db: Session):
    """Undo in-memory reservations made before a failure (best effort)."""
    for alloc in allocations:
        inv = db.query(Inventory).filter(
            Inventory.product_id == alloc["product_id"],
            Inventory.warehouse  == alloc["warehouse"],
        ).first()
        if inv:
            inv.reserved   = max(0, inv.reserved - alloc["quantity"])
            inv.updated_at = datetime.utcnow()
    db.commit()


@app.post("/v1/inventory/release")
def release(body: ReleaseRequest, db: Session = Depends(get_db)):
    count = _do_release(body.reservation_id, body.order_id, db)
    return {"success": True, "released_count": count}


@app.post("/v1/inventory/ship")
def ship(body: ShipRequest, db: Session = Depends(get_db)):
    reservations = db.query(Reservation).filter(
        Reservation.order_id    == body.order_id,
        Reservation.is_released == False,
        Reservation.is_shipped  == False,
    ).all()

    if not reservations:
        raise HTTPException(404, f"No active reservations found for order {body.order_id}")

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
            product_id=res.product_id,
            warehouse=res.warehouse,
            order_id=body.order_id,
            type="SHIP",
            quantity=res.quantity,
        ))

    db.commit()
    SHIPMENTS_MADE.inc()
    log("Inventory shipped", order_id=body.order_id, items=len(reservations))
    return {"success": True, "shipped_items": len(reservations)}


@app.post("/v1/inventory/restock")
def restock(body: RestockRequest, db: Session = Depends(get_db)):
    inv = db.query(Inventory).filter(
        Inventory.product_id == body.product_id,
        Inventory.warehouse  == body.warehouse,
    ).first()
    if not inv:
        inv = Inventory(product_id=body.product_id, warehouse=body.warehouse, on_hand=0, reserved=0)
        db.add(inv)
    inv.on_hand   += body.quantity
    inv.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(inv)
    log("Inventory restocked", product_id=body.product_id, warehouse=body.warehouse, quantity=body.quantity)
    return _inv_to_out(inv)


@app.get("/v1/inventory/movements", response_model=Paginated)
def list_movements(
    page:       int           = Query(1, ge=1),
    size:       int           = Query(20, ge=1, le=100),
    product_id: Optional[int] = None,
    order_id:   Optional[int] = None,
    type:       Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(InventoryMovement)
    if product_id: q = q.filter(InventoryMovement.product_id == product_id)
    if order_id:   q = q.filter(InventoryMovement.order_id == order_id)
    if type:       q = q.filter(InventoryMovement.type == type)
    total = q.count()
    items = q.order_by(InventoryMovement.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return {
        "data": [
            {"movement_id": m.movement_id, "product_id": m.product_id, "warehouse": m.warehouse,
             "order_id": m.order_id, "type": m.type, "quantity": m.quantity, "created_at": m.created_at}
            for m in items
        ],
        "total": total, "page": page, "size": size,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8002)))

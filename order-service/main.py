"""Order Service — place orders (Reserve→Pay→Ship), cancel orders, idempotency."""
import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import List, Optional

import httpx
import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pythonjsonlogger import jsonlogger
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
import logging

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL      = os.getenv("DATABASE_URL",             "sqlite:///./order.db")
CATALOG_URL       = os.getenv("CATALOG_SERVICE_URL",      "http://catalog-service:8001")
INVENTORY_URL     = os.getenv("INVENTORY_SERVICE_URL",    "http://inventory-service:8002")
PAYMENT_URL       = os.getenv("PAYMENT_SERVICE_URL",      "http://payment-service:8004")
SHIPPING_URL      = os.getenv("SHIPPING_SERVICE_URL",     "http://shipping-service:8005")
NOTIFICATION_URL  = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8006")
SHIPPING_CHARGE   = Decimal("100.00")
TAX_RATE          = Decimal("0.05")

# ── Logging ──────────────────────────────────────────────────────────────────
_logger = logging.getLogger("order")
_h = logging.StreamHandler()
_h.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
_logger.addHandler(_h)
_logger.setLevel(logging.INFO)


def log(msg: str, level: str = "info", **ctx):
    ctx["service"] = "order-service"
    getattr(_logger, level)(msg, extra=ctx)


# ── Database ──────────────────────────────────────────────────────────────────
engine       = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base         = declarative_base()


class Order(Base):
    __tablename__ = "orders"
    order_id          = Column(Integer, primary_key=True, index=True)
    customer_id       = Column(Integer, index=True, nullable=False)
    # PENDING → CONFIRMED | FAILED | CANCELLED
    order_status      = Column(String(20), default="PENDING")
    # PENDING → SUCCESS | FAILED | REFUNDED
    payment_status    = Column(String(20), default="PENDING")
    order_total       = Column(Float)
    totals_signature  = Column(String(64))   # SHA-256 of pricing components
    idempotency_key   = Column(String(200), unique=True, index=True)
    payment_method    = Column(String(50), default="CARD")
    created_at        = Column(DateTime, default=datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.utcnow)


class OrderItem(Base):
    __tablename__ = "order_items"
    order_item_id = Column(Integer, primary_key=True, index=True)
    order_id      = Column(Integer, index=True, nullable=False)
    product_id    = Column(Integer)
    sku           = Column(String(50))
    product_name  = Column(String(200))    # read model (copied from Catalog)
    quantity      = Column(Integer)
    unit_price    = Column(Float)          # copied from Catalog at order time


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Metrics ───────────────────────────────────────────────────────────────────
ORDERS_PLACED    = Counter("orders_placed_total",    "Orders successfully placed")
ORDERS_CANCELLED = Counter("orders_cancelled_total", "Orders cancelled")
ORDERS_FAILED    = Counter("orders_failed_total",    "Orders failed (payment/stock)")


# ── Schemas ───────────────────────────────────────────────────────────────────
class OrderItemRequest(BaseModel):
    sku:      str
    quantity: int


class OrderRequest(BaseModel):
    customer_id:    int
    items:          List[OrderItemRequest]
    payment_method: Optional[str] = "CARD"


class OrderItemOut(BaseModel):
    order_item_id: int
    order_id:      int
    product_id:    Optional[int]
    sku:           str
    product_name:  Optional[str]
    quantity:      int
    unit_price:    float

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    order_id:         int
    customer_id:      int
    order_status:     str
    payment_status:   str
    order_total:      Optional[float]
    totals_signature: Optional[str]
    payment_method:   Optional[str]
    created_at:       datetime
    updated_at:       datetime

    class Config:
        from_attributes = True


class OrderDetailOut(OrderOut):
    items: List[OrderItemOut] = []


class Paginated(BaseModel):
    data:  List[OrderOut]
    total: int
    page:  int
    size:  int


# ── Seed ──────────────────────────────────────────────────────────────────────
def seed():
    db = SessionLocal()
    try:
        if db.query(Order).count() > 0:
            return
        o_csv  = os.getenv("ORDERS_CSV",      "/data/eci_orders_indian.csv")
        oi_csv = os.getenv("ORDER_ITEMS_CSV",  "/data/eci_order_items_indian.csv")
        if not (os.path.exists(o_csv) and os.path.exists(oi_csv)):
            return
        df_o  = pd.read_csv(o_csv)
        df_oi = pd.read_csv(oi_csv)
        for _, r in df_o.iterrows():
            db.merge(Order(
                order_id=int(r["order_id"]),
                customer_id=int(r["customer_id"]),
                order_status=str(r["order_status"]),
                payment_status=str(r["payment_status"]),
                order_total=float(r["order_total"]),
                idempotency_key=f"seed-{r['order_id']}",
                created_at=pd.to_datetime(r["created_at"]).to_pydatetime(),
                updated_at=pd.to_datetime(r["created_at"]).to_pydatetime(),
            ))
        for _, r in df_oi.iterrows():
            db.merge(OrderItem(
                order_item_id=int(r["order_item_id"]),
                order_id=int(r["order_id"]),
                product_id=int(r["product_id"]),
                sku=str(r["sku"]),
                quantity=int(r["quantity"]),
                unit_price=float(r["unit_price"]),
            ))
        db.commit()
        log("Orders seeded", orders=len(df_o), items=len(df_oi))
    except Exception as exc:
        log(f"Seed error: {exc}", level="error")
    finally:
        db.close()


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed()
    yield


app = FastAPI(title="Order Service", version="1.0.0", lifespan=lifespan)


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


# ── Internal helpers ──────────────────────────────────────────────────────────
def _notify(type_: str, customer_id: int, order_id: int, message: str):
    try:
        httpx.post(f"{NOTIFICATION_URL}/v1/notifications", json={
            "notification_type": type_,
            "recipient_id":      customer_id,
            "message":           message,
            "reference_id":      str(order_id),
        }, timeout=3)
    except Exception:
        pass  # non-critical


def _compute_totals(items: List[dict]) -> tuple[Decimal, Decimal, Decimal, Decimal, str]:
    """Returns (subtotal, tax, shipping, total, signature)."""
    subtotal = sum(Decimal(str(i["unit_price"])) * i["quantity"] for i in items)
    tax      = (subtotal * TAX_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    total    = (subtotal + tax + SHIPPING_CHARGE).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    sig_data = {"subtotal": str(subtotal), "tax": str(tax), "shipping": str(SHIPPING_CHARGE), "total": str(total)}
    signature = hashlib.sha256(json.dumps(sig_data, sort_keys=True).encode()).hexdigest()
    return subtotal, tax, SHIPPING_CHARGE, total, signature


def _order_to_detail(order: Order, db: Session) -> dict:
    items = db.query(OrderItem).filter(OrderItem.order_id == order.order_id).all()
    base  = OrderOut.model_validate(order).model_dump()
    base["items"] = [OrderItemOut.model_validate(i).model_dump() for i in items]
    return base


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "healthy", "service": "order-service", "timestamp": datetime.utcnow().isoformat()}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/orders", response_model=Paginated)
def list_orders(
    page:          int           = Query(1, ge=1),
    size:          int           = Query(20, ge=1, le=100),
    customer_id:   Optional[int] = None,
    order_status:  Optional[str] = None,
    payment_status:Optional[str] = None,
    db: Session = Depends(get_db),
):
    q = db.query(Order)
    if customer_id:    q = q.filter(Order.customer_id    == customer_id)
    if order_status:   q = q.filter(Order.order_status   == order_status)
    if payment_status: q = q.filter(Order.payment_status == payment_status)
    total = q.count()
    items = q.order_by(Order.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return {"data": items, "total": total, "page": page, "size": size}


@app.get("/v1/orders/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.order_id == order_id).first()
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")
    return _order_to_detail(order, db)


@app.post("/v1/orders", status_code=201)
def place_order(
    body:              OrderRequest,
    idempotency_key:   Optional[str] = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    """Place a new order: Reserve → Pay → Ship."""
    idem_key = idempotency_key or str(uuid.uuid4())

    # ── Idempotency check ─────────────────────────────────────────────────────
    existing = db.query(Order).filter(Order.idempotency_key == idem_key).first()
    if existing:
        log("Idempotent order hit", idempotency_key=idem_key)
        return _order_to_detail(existing, db)

    # ── Step 1: Create order in PENDING state ─────────────────────────────────
    order = Order(
        customer_id=body.customer_id,
        order_status="PENDING",
        payment_status="PENDING",
        idempotency_key=idem_key,
        payment_method=(body.payment_method or "CARD").upper(),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    order_id       = order.order_id
    reservation_id = f"order-{order_id}"

    # ── Step 2: Fetch authoritative pricing from Catalog ──────────────────────
    enriched_items = []
    for req_item in body.items:
        try:
            r = httpx.get(f"{CATALOG_URL}/v1/products/sku/{req_item.sku}", timeout=5)
            r.raise_for_status()
            product = r.json()
        except Exception as exc:
            _fail_order(order, db, "PENDING")
            raise HTTPException(503, f"Catalog service unavailable for SKU '{req_item.sku}': {exc}")

        if not product.get("is_active", True):
            _fail_order(order, db, "PENDING")
            raise HTTPException(400, f"Product '{req_item.sku}' is inactive")

        enriched_items.append({
            "product_id": product["product_id"],
            "sku":        req_item.sku,
            "quantity":   req_item.quantity,
            "unit_price": product["price"],
            "product_name": product["name"],
        })

    # ── Step 3: Reserve inventory ─────────────────────────────────────────────
    reserve_payload = {
        "reservation_id": reservation_id,
        "order_id":       order_id,
        "items":          [{"product_id": i["product_id"], "sku": i["sku"], "quantity": i["quantity"]} for i in enriched_items],
    }
    try:
        r = httpx.post(f"{INVENTORY_URL}/v1/inventory/reserve", json=reserve_payload, timeout=10)
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _fail_order(order, db, "PENDING")
        detail = exc.response.json() if exc.response.content else str(exc)
        raise HTTPException(exc.response.status_code, f"Inventory reservation failed: {detail}")
    except Exception as exc:
        _fail_order(order, db, "PENDING")
        raise HTTPException(503, f"Inventory service unavailable: {exc}")

    # ── Step 4: Compute totals with banker's rounding ─────────────────────────
    _, _, _, total, signature = _compute_totals(enriched_items)

    # Save order items (pricing snapshot / read model)
    for item in enriched_items:
        db.add(OrderItem(
            order_id=order_id,
            product_id=item["product_id"],
            sku=item["sku"],
            product_name=item["product_name"],
            quantity=item["quantity"],
            unit_price=float(item["unit_price"]),
        ))
    order.order_total      = float(total)
    order.totals_signature = signature
    order.updated_at       = datetime.utcnow()
    db.commit()

    # ── Step 5: Charge payment ────────────────────────────────────────────────
    payment_payload = {
        "order_id":        order_id,
        "amount":          float(total),
        "method":          order.payment_method,
        "idempotency_key": f"order-{order_id}-charge",
    }
    try:
        r = httpx.post(f"{PAYMENT_URL}/v1/payments/charge", json=payment_payload, timeout=10)
        # 402 means payment declined — we want to handle it explicitly
        if r.status_code not in (200, 201, 402):
            r.raise_for_status()
        payment_data = r.json()
    except httpx.HTTPStatusError as exc:
        _release_inventory(reservation_id, order_id)
        _fail_order(order, db, "FAILED")
        detail = exc.response.json() if exc.response.content else str(exc)
        raise HTTPException(exc.response.status_code, f"Payment service error: {detail}")
    except Exception as exc:
        _release_inventory(reservation_id, order_id)
        _fail_order(order, db, "FAILED")
        raise HTTPException(503, f"Payment service unavailable: {exc}")

    # Payment gateway declined
    if r.status_code == 402 or payment_data.get("status") == "FAILED":
        _release_inventory(reservation_id, order_id)
        order.order_status   = "FAILED"
        order.payment_status = "FAILED"
        order.updated_at     = datetime.utcnow()
        db.commit()
        ORDERS_FAILED.inc()
        _notify("PAYMENT_FAILED", body.customer_id, order_id, f"Payment failed for order {order_id}.")
        raise HTTPException(402, f"Payment declined for order {order_id}")

    # ── Step 6: Confirm order ─────────────────────────────────────────────────
    order.order_status   = "CONFIRMED"
    order.payment_status = "SUCCESS"
    order.updated_at     = datetime.utcnow()
    db.commit()
    ORDERS_PLACED.inc()
    log("Order confirmed", order_id=order_id, total=float(total))

    # ── Step 7: Create shipment (non-critical) ────────────────────────────────
    try:
        httpx.post(f"{SHIPPING_URL}/v1/shipments",
                   json={"order_id": order_id},
                   timeout=5)
    except Exception as exc:
        log(f"Shipping service error (non-critical): {exc}", level="warning")

    # ── Step 8: Send notification (non-critical) ──────────────────────────────
    _notify("ORDER_CONFIRMED", body.customer_id, order_id,
            f"Order #{order_id} confirmed! Total ₹{total}. Your items are being prepared.")

    return _order_to_detail(order, db)


@app.post("/v1/orders/{order_id}/cancel")
def cancel_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.order_id == order_id).first()
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")
    if order.order_status in ("CANCELLED",):
        return {"message": "Order already cancelled", "order_id": order_id}
    if order.order_status == "DELIVERED":
        raise HTTPException(400, "Cannot cancel a delivered order")

    reservation_id = f"order-{order_id}"

    # Release inventory reservations
    _release_inventory(reservation_id, order_id)

    # Refund if payment was successful
    if order.payment_status == "SUCCESS":
        try:
            # Find payment for this order
            r = httpx.get(f"{PAYMENT_URL}/v1/payments?order_id={order_id}&status=SUCCESS", timeout=5)
            r.raise_for_status()
            payments = r.json().get("data", [])
            for p in payments:
                httpx.post(
                    f"{PAYMENT_URL}/v1/payments/{p['payment_id']}/refund",
                    json={"reason": "Order cancelled by customer", "idempotency_key": f"refund-{order_id}"},
                    timeout=5,
                )
            order.payment_status = "REFUNDED"
        except Exception as exc:
            log(f"Refund error for order {order_id}: {exc}", level="error")

    order.order_status = "CANCELLED"
    order.updated_at   = datetime.utcnow()
    db.commit()

    ORDERS_CANCELLED.inc()
    log("Order cancelled", order_id=order_id)
    _notify("ORDER_CANCELLED", order.customer_id, order_id,
            f"Order #{order_id} has been cancelled. Refund will be processed if applicable.")
    return {"message": "Order cancelled", "order_id": order_id, "payment_status": order.payment_status}


# ── Private helpers ───────────────────────────────────────────────────────────
def _fail_order(order: Order, db: Session, payment_status: str):
    order.order_status   = "FAILED"
    order.payment_status = payment_status
    order.updated_at     = datetime.utcnow()
    db.commit()
    ORDERS_FAILED.inc()


def _release_inventory(reservation_id: str, order_id: int):
    try:
        httpx.post(
            f"{INVENTORY_URL}/v1/inventory/release",
            json={"reservation_id": reservation_id, "order_id": order_id},
            timeout=5,
        )
    except Exception as exc:
        log(f"Inventory release error: {exc}", level="error")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8003)))

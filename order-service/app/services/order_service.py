import hashlib
import json
import logging
import os
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal

import httpx
import pandas as pd
from fastapi import HTTPException
from prometheus_client import Counter
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Order, OrderItem
from app.schemas import OrderRequest, OrderOut, OrderItemOut

logger = logging.getLogger("order")

ORDERS_PLACED    = Counter("orders_placed_total",    "Orders successfully placed")
ORDERS_CANCELLED = Counter("orders_cancelled_total", "Orders cancelled")
ORDERS_FAILED    = Counter("orders_failed_total",    "Orders failed")


# ── Seed ──────────────────────────────────────────────────────────────────────
def seed(db_factory):
    db = db_factory()
    try:
        if db.query(Order).count() > 0:
            return
        if not (os.path.exists(settings.ORDERS_CSV) and os.path.exists(settings.ORDER_ITEMS_CSV)):
            return
        df_o  = pd.read_csv(settings.ORDERS_CSV)
        df_oi = pd.read_csv(settings.ORDER_ITEMS_CSV)
        for _, r in df_o.iterrows():
            db.merge(Order(
                order_id=int(r["order_id"]), customer_id=int(r["customer_id"]),
                order_status=str(r["order_status"]), payment_status=str(r["payment_status"]),
                order_total=float(r["order_total"]),
                idempotency_key=f"seed-{r['order_id']}",
                created_at=pd.to_datetime(r["created_at"]).to_pydatetime(),
                updated_at=pd.to_datetime(r["created_at"]).to_pydatetime(),
            ))
        for _, r in df_oi.iterrows():
            db.merge(OrderItem(
                order_item_id=int(r["order_item_id"]), order_id=int(r["order_id"]),
                product_id=int(r["product_id"]), sku=str(r["sku"]),
                quantity=int(r["quantity"]), unit_price=float(r["unit_price"]),
            ))
        db.commit()
        logger.info("Seeded %d orders, %d items", len(df_o), len(df_oi))
    except Exception as exc:
        logger.error("Seed error: %s", exc)
    finally:
        db.close()


# ── Helpers ───────────────────────────────────────────────────────────────────
def to_detail(order: Order, db: Session) -> dict:
    items = db.query(OrderItem).filter(OrderItem.order_id == order.order_id).all()
    base  = OrderOut.model_validate(order).model_dump()
    base["items"] = [OrderItemOut.model_validate(i).model_dump() for i in items]
    return base


def compute_totals(items: list) -> tuple:
    subtotal  = sum(Decimal(str(i["unit_price"])) * i["quantity"] for i in items)
    tax       = (subtotal * settings.TAX_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    total     = (subtotal + tax + settings.SHIPPING_CHARGE).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    sig_data  = {"subtotal": str(subtotal), "tax": str(tax),
                 "shipping": str(settings.SHIPPING_CHARGE), "total": str(total)}
    signature = hashlib.sha256(json.dumps(sig_data, sort_keys=True).encode()).hexdigest()
    return subtotal, tax, settings.SHIPPING_CHARGE, total, signature


def notify(type_: str, customer_id: int, order_id: int, message: str):
    try:
        httpx.post(f"{settings.NOTIFICATION_URL}/v1/notifications", json={
            "notification_type": type_, "recipient_id": customer_id,
            "message": message, "reference_id": str(order_id),
        }, timeout=3)
    except Exception:
        pass


def release_inventory(reservation_id: str, order_id: int):
    try:
        httpx.post(f"{settings.INVENTORY_URL}/v1/inventory/release",
                   json={"reservation_id": reservation_id, "order_id": order_id}, timeout=5)
    except Exception as exc:
        logger.error("Inventory release error: %s", exc)


def fail_order(order: Order, db: Session, payment_status: str):
    order.order_status   = "FAILED"
    order.payment_status = payment_status
    order.updated_at     = datetime.utcnow()
    db.commit()
    ORDERS_FAILED.inc()


# ── Business operations ────────────────────────────────────────────────────────
def list_orders(db: Session, page: int, size: int,
                customer_id=None, order_status=None, payment_status=None) -> dict:
    q = db.query(Order)
    if customer_id:    q = q.filter(Order.customer_id    == customer_id)
    if order_status:   q = q.filter(Order.order_status   == order_status)
    if payment_status: q = q.filter(Order.payment_status == payment_status)
    total = q.count()
    items = q.order_by(Order.created_at.desc()).offset((page-1)*size).limit(size).all()
    return {"data": items, "total": total, "page": page, "size": size}


def get_order(db: Session, order_id: int) -> dict:
    order = db.query(Order).filter(Order.order_id == order_id).first()
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")
    return to_detail(order, db)


def place_order(db: Session, body: OrderRequest, idem_key: str) -> dict:
    # Idempotency
    existing = db.query(Order).filter(Order.idempotency_key == idem_key).first()
    if existing:
        logger.info("Idempotent order hit: key=%s", idem_key)
        return to_detail(existing, db)

    # Step 1: Create PENDING order
    order = Order(customer_id=body.customer_id, idempotency_key=idem_key,
                  payment_method=(body.payment_method or "CARD").upper())
    db.add(order)
    db.commit()
    db.refresh(order)
    order_id       = order.order_id
    reservation_id = f"order-{order_id}"

    # Step 2: Get authoritative pricing from Catalog
    enriched = []
    for req in body.items:
        try:
            r = httpx.get(f"{settings.CATALOG_URL}/v1/products/sku/{req.sku}", timeout=5)
            r.raise_for_status()
            p = r.json()
        except Exception as exc:
            fail_order(order, db, "PENDING")
            raise HTTPException(503, f"Catalog unavailable for SKU '{req.sku}': {exc}")
        if not p.get("is_active", True):
            fail_order(order, db, "PENDING")
            raise HTTPException(400, f"Product '{req.sku}' is inactive")
        enriched.append({"product_id": p["product_id"], "sku": req.sku,
                          "quantity": req.quantity, "unit_price": p["price"],
                          "product_name": p["name"]})

    # Step 3: Reserve inventory
    try:
        r = httpx.post(f"{settings.INVENTORY_URL}/v1/inventory/reserve", json={
            "reservation_id": reservation_id, "order_id": order_id,
            "items": [{"product_id": i["product_id"], "sku": i["sku"],
                       "quantity": i["quantity"]} for i in enriched],
        }, timeout=10)
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        fail_order(order, db, "PENDING")
        raise HTTPException(exc.response.status_code,
                            f"Inventory reservation failed: {exc.response.json()}")
    except Exception as exc:
        fail_order(order, db, "PENDING")
        raise HTTPException(503, f"Inventory service unavailable: {exc}")

    # Step 4: Compute totals
    _, _, _, total, signature = compute_totals(enriched)

    for item in enriched:
        db.add(OrderItem(order_id=order_id, product_id=item["product_id"],
                         sku=item["sku"], product_name=item["product_name"],
                         quantity=item["quantity"], unit_price=float(item["unit_price"])))
    order.order_total      = float(total)
    order.totals_signature = signature
    order.updated_at       = datetime.utcnow()
    db.commit()

    # Step 5: Charge payment
    try:
        r = httpx.post(f"{settings.PAYMENT_URL}/v1/payments/charge", json={
            "order_id": order_id, "amount": float(total),
            "method": order.payment_method, "idempotency_key": f"order-{order_id}-charge",
        }, timeout=10)
        if r.status_code not in (200, 201, 402):
            r.raise_for_status()
        payment_data = r.json()
    except httpx.HTTPStatusError as exc:
        release_inventory(reservation_id, order_id)
        fail_order(order, db, "FAILED")
        raise HTTPException(exc.response.status_code, f"Payment error: {exc.response.json()}")
    except Exception as exc:
        release_inventory(reservation_id, order_id)
        fail_order(order, db, "FAILED")
        raise HTTPException(503, f"Payment service unavailable: {exc}")

    if r.status_code == 402 or payment_data.get("status") == "FAILED":
        release_inventory(reservation_id, order_id)
        fail_order(order, db, "FAILED")
        notify("PAYMENT_FAILED", body.customer_id, order_id,
               f"Payment failed for order {order_id}.")
        raise HTTPException(402, f"Payment declined for order {order_id}")

    # Step 6: Confirm
    order.order_status   = "CONFIRMED"
    order.payment_status = "SUCCESS"
    order.updated_at     = datetime.utcnow()
    db.commit()
    ORDERS_PLACED.inc()
    logger.info("Order confirmed: id=%d total=%.2f", order_id, float(total))

    # Step 7: Create shipment (non-critical)
    try:
        httpx.post(f"{settings.SHIPPING_URL}/v1/shipments",
                   json={"order_id": order_id}, timeout=5)
    except Exception as exc:
        logger.warning("Shipping service error (non-critical): %s", exc)

    notify("ORDER_CONFIRMED", body.customer_id, order_id,
           f"Order #{order_id} confirmed! Total ₹{total}. Your items are being prepared.")
    return to_detail(order, db)


def cancel_order(db: Session, order_id: int) -> dict:
    order = db.query(Order).filter(Order.order_id == order_id).first()
    if not order:
        raise HTTPException(404, f"Order {order_id} not found")
    if order.order_status == "CANCELLED":
        return {"message": "Order already cancelled", "order_id": order_id}
    if order.order_status == "DELIVERED":
        raise HTTPException(400, "Cannot cancel a delivered order")

    release_inventory(f"order-{order_id}", order_id)

    if order.payment_status == "SUCCESS":
        try:
            r = httpx.get(f"{settings.PAYMENT_URL}/v1/payments?order_id={order_id}&status=SUCCESS",
                          timeout=5)
            for p in r.json().get("data", []):
                httpx.post(f"{settings.PAYMENT_URL}/v1/payments/{p['payment_id']}/refund",
                           json={"reason": "Order cancelled", "idempotency_key": f"refund-{order_id}"},
                           timeout=5)
            order.payment_status = "REFUNDED"
        except Exception as exc:
            logger.error("Refund error for order %d: %s", order_id, exc)

    order.order_status = "CANCELLED"
    order.updated_at   = datetime.utcnow()
    db.commit()
    ORDERS_CANCELLED.inc()
    logger.info("Order cancelled: id=%d", order_id)
    notify("ORDER_CANCELLED", order.customer_id, order_id,
           f"Order #{order_id} has been cancelled.")
    return {"message": "Order cancelled", "order_id": order_id,
            "payment_status": order.payment_status}

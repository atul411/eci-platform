from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.schemas import (
    InventoryOut, MovementOut, Paginated,
    ReleaseRequest, ReserveRequest, RestockRequest, ShipRequest,
)
import app.services.inventory_service as svc

router = APIRouter(prefix="/v1/inventory", tags=["Inventory"])


@router.get("", response_model=Paginated)
def list_inventory(
    page:       int           = Query(1, ge=1),
    size:       int           = Query(20, ge=1, le=100),
    product_id: Optional[int] = None,
    warehouse:  Optional[str] = None,
    db: Session = Depends(get_db),
):
    return svc.list_inventory(db, page, size, product_id, warehouse)


@router.get("/product/{product_id}")
def get_stock_for_product(product_id: int, db: Session = Depends(get_db)):
    return svc.get_stock_for_product(db, product_id)


@router.post("/reserve", status_code=200)
def reserve(body: ReserveRequest, db: Session = Depends(get_db)):
    expires_at = datetime.utcnow() + timedelta(minutes=settings.RESERVATION_TTL_MINS)
    return svc.reserve(
        db, body.reservation_id, body.order_id,
        body.items,
        body.allow_backorder, expires_at,
    )


@router.post("/release", status_code=200)
def release(body: ReleaseRequest, db: Session = Depends(get_db)):
    released = svc.do_release(body.reservation_id, body.order_id, db)
    return {"released": released, "reservation_id": body.reservation_id}


@router.post("/ship", status_code=200)
def ship_order(body: ShipRequest, db: Session = Depends(get_db)):
    return svc.ship_order(db, body.order_id)


@router.post("/restock", status_code=200)
def restock(body: RestockRequest, db: Session = Depends(get_db)):
    return svc.restock(db, body.product_id, body.warehouse, body.quantity)


@router.get("/movements", response_model=Paginated)
def list_movements(
    page:       int           = Query(1, ge=1),
    size:       int           = Query(20, ge=1, le=100),
    product_id: Optional[int] = None,
    order_id:   Optional[int] = None,
    db: Session = Depends(get_db),
):
    return svc.list_movements(db, page, size, product_id, order_id)

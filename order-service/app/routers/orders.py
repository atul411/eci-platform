from typing import Optional

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import OrderRequest, Paginated
import app.services.order_service as svc

router = APIRouter(prefix="/v1/orders", tags=["Orders"])


@router.get("", response_model=Paginated)
def list_orders(
    page:           int           = Query(1, ge=1),
    size:           int           = Query(20, ge=1, le=100),
    customer_id:    Optional[int] = None,
    order_status:   Optional[str] = None,
    payment_status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return svc.list_orders(db, page, size, customer_id, order_status, payment_status)


@router.post("", status_code=201)
def place_order(
    body:            OrderRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    return svc.place_order(db, body, idempotency_key)


@router.get("/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db)):
    return svc.get_order(db, order_id)


@router.post("/{order_id}/cancel")
def cancel_order(order_id: int, db: Session = Depends(get_db)):
    return svc.cancel_order(db, order_id)

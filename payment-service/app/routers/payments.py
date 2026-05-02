from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ChargeRequest, Paginated, PaymentOut, RefundRequest
import app.services.payment_service as svc

router = APIRouter(prefix="/v1/payments", tags=["Payments"])


@router.get("", response_model=Paginated)
def list_payments(
    page:     int           = Query(1, ge=1),
    size:     int           = Query(20, ge=1, le=100),
    order_id: Optional[int] = None,
    status:   Optional[str] = None,
    db: Session = Depends(get_db),
):
    return svc.list_payments(db, page, size, order_id, status)


@router.post("/charge", response_model=PaymentOut, status_code=201)
def charge(body: ChargeRequest, db: Session = Depends(get_db)):
    return svc.charge(db, body)


@router.get("/{payment_id}", response_model=PaymentOut)
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    return svc.get_payment(db, payment_id)


@router.post("/{payment_id}/refund", response_model=PaymentOut)
def refund(payment_id: int, body: RefundRequest, db: Session = Depends(get_db)):
    return svc.refund(db, payment_id, body)

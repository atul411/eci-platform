from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import Paginated, ShipmentCreate, ShipmentOut, StatusUpdate
import app.services.shipping_service as svc

router = APIRouter(prefix="/v1/shipments", tags=["Shipments"])


@router.get("", response_model=Paginated)
def list_shipments(
    page:     int           = Query(1, ge=1),
    size:     int           = Query(20, ge=1, le=100),
    order_id: Optional[int] = None,
    status:   Optional[str] = None,
    db: Session = Depends(get_db),
):
    return svc.list_shipments(db, page, size, order_id, status)


@router.post("", response_model=ShipmentOut, status_code=201)
def create_shipment(body: ShipmentCreate, db: Session = Depends(get_db)):
    return svc.create_shipment(db, body)


@router.get("/{shipment_id}", response_model=ShipmentOut)
def get_shipment(shipment_id: int, db: Session = Depends(get_db)):
    return svc.get_shipment(db, shipment_id)


@router.patch("/{shipment_id}/status", response_model=ShipmentOut)
def update_status(shipment_id: int, body: StatusUpdate, db: Session = Depends(get_db)):
    return svc.update_status(db, shipment_id, body)

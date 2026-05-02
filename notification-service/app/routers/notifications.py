from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import NotificationOut, NotificationRequest, Paginated
import app.services.notification_service as svc

router = APIRouter(prefix="/v1/notifications", tags=["Notifications"])


@router.get("", response_model=Paginated)
def list_notifications(
    page:              int           = Query(1, ge=1),
    size:              int           = Query(20, ge=1, le=100),
    notification_type: Optional[str] = None,
    recipient_id:      Optional[int] = None,
    db: Session = Depends(get_db),
):
    return svc.list_notifications(db, page, size, notification_type, recipient_id)


@router.post("", response_model=NotificationOut, status_code=201)
def send_notification(body: NotificationRequest, db: Session = Depends(get_db)):
    return svc.send_notification(db, body)


@router.get("/{notification_id}", response_model=NotificationOut)
def get_notification(notification_id: int, db: Session = Depends(get_db)):
    return svc.get_notification(db, notification_id)

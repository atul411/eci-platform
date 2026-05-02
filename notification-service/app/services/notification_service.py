import logging
import re

from fastapi import HTTPException
from prometheus_client import Counter
from sqlalchemy.orm import Session

from app.models import NotificationLog
from app.schemas import NotificationRequest

logger = logging.getLogger("notification")

NOTIFICATIONS_SENT   = Counter("notifications_sent_total",   "Notifications sent", ["type"])
NOTIFICATIONS_FAILED = Counter("notifications_failed_total", "Notifications failed")


def _mask_email(email: str) -> str:
    if not email or "@" not in email:
        return "***"
    u, d = email.split("@", 1)
    return u[:2] + "***@" + d


def _mask_phone(phone: str) -> str:
    return re.sub(r"\d(?=\d{4})", "*", phone) if phone else "***"


def list_notifications(db: Session, page: int, size: int,
                       notification_type=None, recipient_id=None) -> dict:
    q = db.query(NotificationLog)
    if notification_type: q = q.filter(NotificationLog.notification_type == notification_type)
    if recipient_id:      q = q.filter(NotificationLog.recipient_id == recipient_id)
    total = q.count()
    items = q.order_by(NotificationLog.created_at.desc()).offset((page-1)*size).limit(size).all()
    return {"data": items, "total": total, "page": page, "size": size}


def get_notification(db: Session, notification_id: int) -> NotificationLog:
    n = db.query(NotificationLog).filter(
        NotificationLog.notification_id == notification_id
    ).first()
    if not n:
        raise HTTPException(404, f"Notification {notification_id} not found")
    return n


def send_notification(db: Session, body: NotificationRequest) -> NotificationLog:
    n = NotificationLog(
        notification_type=body.notification_type,
        channel=body.channel or "EMAIL",
        recipient_id=body.recipient_id,
        recipient_contact=body.recipient_contact,
        message=body.message,
        status="SENT",
        reference_id=body.reference_id,
    )
    db.add(n)
    db.commit()
    db.refresh(n)

    NOTIFICATIONS_SENT.labels(type=body.notification_type).inc()

    # Mask sensitive fields before logging
    contact = body.recipient_contact or ""
    masked  = (_mask_email(contact) if body.channel == "EMAIL"
               else _mask_phone(contact) if body.channel == "SMS"
               else "***")
    logger.info("Notification sent: id=%d type=%s recipient_id=%s contact=%s",
                n.notification_id, body.notification_type, body.recipient_id, masked)
    return n

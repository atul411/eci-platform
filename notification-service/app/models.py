from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database import Base


class NotificationLog(Base):
    __tablename__ = "notifications_log"
    notification_id   = Column(Integer, primary_key=True, index=True)
    notification_type = Column(String(50))    # ORDER_CONFIRMED|PAYMENT_FAILED|SHIPPED|LOW_STOCK ...
    channel           = Column(String(20), default="EMAIL")  # EMAIL|SMS
    recipient_id      = Column(Integer)
    recipient_contact = Column(String(200))   # stored as-is; masked in logs
    message           = Column(Text)
    status            = Column(String(20), default="SENT")   # SENT|FAILED
    reference_id      = Column(String(100))
    created_at        = Column(DateTime, default=datetime.utcnow)

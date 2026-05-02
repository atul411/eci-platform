from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String

from app.database import Base


class Payment(Base):
    __tablename__ = "payments"
    payment_id = Column(Integer, primary_key=True, index=True)
    order_id   = Column(Integer, index=True, nullable=False)
    amount     = Column(Float, nullable=False)
    method     = Column(String(50), default="CARD")   # CARD|UPI|COD|NETBANKING
    status     = Column(String(20), default="PENDING") # PENDING|SUCCESS|FAILED|REFUNDED
    reference  = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    key        = Column(String(200), primary_key=True)
    payment_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

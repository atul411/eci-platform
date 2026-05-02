from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.database import Base


class Shipment(Base):
    __tablename__ = "shipments"
    shipment_id  = Column(Integer, primary_key=True, index=True)
    order_id     = Column(Integer, index=True, nullable=False)
    carrier      = Column(String(100))
    status       = Column(String(20), default="PENDING")  # PENDING|PACKED|SHIPPED|DELIVERED|CANCELLED
    tracking_no  = Column(String(100))
    shipped_at   = Column(DateTime)
    delivered_at = Column(DateTime)
    created_at   = Column(DateTime, default=datetime.utcnow)

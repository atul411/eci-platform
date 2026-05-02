from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String

from app.database import Base


class Order(Base):
    __tablename__ = "orders"
    order_id         = Column(Integer, primary_key=True, index=True)
    customer_id      = Column(Integer, index=True, nullable=False)
    order_status     = Column(String(20), default="PENDING")   # PENDING|CONFIRMED|FAILED|CANCELLED
    payment_status   = Column(String(20), default="PENDING")   # PENDING|SUCCESS|FAILED|REFUNDED
    order_total      = Column(Float)
    totals_signature = Column(String(64))   # SHA-256 tamper-proofing hash
    idempotency_key  = Column(String(200), unique=True, index=True)
    payment_method   = Column(String(50), default="CARD")
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow)


class OrderItem(Base):
    __tablename__ = "order_items"
    order_item_id = Column(Integer, primary_key=True, index=True)
    order_id      = Column(Integer, index=True, nullable=False)
    product_id    = Column(Integer)
    sku           = Column(String(50))
    product_name  = Column(String(200))   # pricing snapshot copied from Catalog
    quantity      = Column(Integer)
    unit_price    = Column(Float)         # authoritative price at time of order

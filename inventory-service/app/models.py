from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String

from app.database import Base


class Inventory(Base):
    __tablename__ = "inventory"
    inventory_id = Column(Integer, primary_key=True, index=True)
    product_id   = Column(Integer, index=True, nullable=False)
    warehouse    = Column(String(50), nullable=False)
    on_hand      = Column(Integer, default=0)
    reserved     = Column(Integer, default=0)
    updated_at   = Column(DateTime, default=datetime.utcnow)


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    movement_id = Column(Integer, primary_key=True, index=True)
    product_id  = Column(Integer, index=True)
    warehouse   = Column(String(50))
    order_id    = Column(Integer, index=True)
    type        = Column(String(20))    # RESERVE | RELEASE | SHIP
    quantity    = Column(Integer)
    created_at  = Column(DateTime, default=datetime.utcnow)


class Reservation(Base):
    """Tracks active reservations for TTL expiry management."""
    __tablename__ = "reservations"
    id             = Column(Integer, primary_key=True)
    reservation_id = Column(String(200), index=True)
    product_id     = Column(Integer)
    warehouse      = Column(String(50))
    quantity       = Column(Integer)
    order_id       = Column(Integer, index=True)
    expires_at     = Column(DateTime)
    is_released    = Column(Boolean, default=False)
    is_shipped     = Column(Boolean, default=False)
    created_at     = Column(DateTime, default=datetime.utcnow)

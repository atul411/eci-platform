from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String

from app.database import Base


class Product(Base):
    __tablename__ = "products"
    product_id = Column(Integer, primary_key=True, index=True)
    sku        = Column(String(50), unique=True, index=True, nullable=False)
    name       = Column(String(200), nullable=False)
    category   = Column(String(100))
    price      = Column(Float, nullable=False)
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

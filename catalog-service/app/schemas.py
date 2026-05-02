from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ProductCreate(BaseModel):
    sku:       str
    name:      str
    category:  str
    price:     float
    is_active: bool = True


class ProductUpdate(BaseModel):
    name:      Optional[str]   = None
    category:  Optional[str]   = None
    price:     Optional[float] = None
    is_active: Optional[bool]  = None


class ProductOut(BaseModel):
    product_id: int
    sku:        str
    name:       str
    category:   str
    price:      float
    is_active:  bool
    created_at: datetime

    class Config:
        from_attributes = True


class Paginated(BaseModel):
    data:  List[ProductOut]
    total: int
    page:  int
    size:  int

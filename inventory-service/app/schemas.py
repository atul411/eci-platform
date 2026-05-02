from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel


class ReserveItem(BaseModel):
    product_id: int
    sku:        Optional[str] = None
    quantity:   int


class ReserveRequest(BaseModel):
    reservation_id:  str
    order_id:        int
    items:           List[ReserveItem]
    allow_backorder: bool = False


class ReleaseRequest(BaseModel):
    reservation_id: str
    order_id:       int


class ShipRequest(BaseModel):
    order_id: int


class RestockRequest(BaseModel):
    product_id: int
    warehouse:  str
    quantity:   int


class InventoryOut(BaseModel):
    inventory_id: int
    product_id:   int
    warehouse:    str
    on_hand:      int
    reserved:     int
    available:    int
    updated_at:   datetime

    class Config:
        from_attributes = True


class MovementOut(BaseModel):
    movement_id: int
    product_id:  int
    warehouse:   str
    order_id:    Optional[int]
    type:        str
    quantity:    int
    created_at:  datetime

    class Config:
        from_attributes = True


class Paginated(BaseModel):
    data:  list
    total: int
    page:  int
    size:  int

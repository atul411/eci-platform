from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class OrderItemRequest(BaseModel):
    sku:      str
    quantity: int


class OrderRequest(BaseModel):
    customer_id:    int
    items:          List[OrderItemRequest]
    payment_method: Optional[str] = "CARD"


class OrderItemOut(BaseModel):
    order_item_id: int
    order_id:      int
    product_id:    Optional[int]
    sku:           str
    product_name:  Optional[str]
    quantity:      int
    unit_price:    float

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    order_id:         int
    customer_id:      int
    order_status:     str
    payment_status:   str
    order_total:      Optional[float]
    totals_signature: Optional[str]
    payment_method:   Optional[str]
    created_at:       datetime
    updated_at:       datetime

    class Config:
        from_attributes = True


class OrderDetailOut(OrderOut):
    items: List[OrderItemOut] = []


class Paginated(BaseModel):
    data:  List[OrderOut]
    total: int
    page:  int
    size:  int

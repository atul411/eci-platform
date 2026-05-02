from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ShipmentCreate(BaseModel):
    order_id: int
    carrier:  Optional[str] = None


class StatusUpdate(BaseModel):
    status: str   # PACKED | SHIPPED | DELIVERED | CANCELLED


class ShipmentOut(BaseModel):
    shipment_id:  int
    order_id:     int
    carrier:      Optional[str]
    status:       str
    tracking_no:  Optional[str]
    shipped_at:   Optional[datetime]
    delivered_at: Optional[datetime]
    created_at:   datetime

    class Config:
        from_attributes = True


class Paginated(BaseModel):
    data:  List[ShipmentOut]
    total: int
    page:  int
    size:  int

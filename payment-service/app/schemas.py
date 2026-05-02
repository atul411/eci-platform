from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class ChargeRequest(BaseModel):
    order_id:        int
    amount:          float
    method:          Optional[str] = "CARD"
    idempotency_key: str


class RefundRequest(BaseModel):
    reason:          Optional[str] = None
    idempotency_key: str


class PaymentOut(BaseModel):
    payment_id: int
    order_id:   int
    amount:     float
    method:     str
    status:     str
    reference:  Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class Paginated(BaseModel):
    data:  List[PaymentOut]
    total: int
    page:  int
    size:  int

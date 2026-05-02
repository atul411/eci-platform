from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class NotificationRequest(BaseModel):
    notification_type: str
    channel:           Optional[str] = "EMAIL"
    recipient_id:      Optional[int] = None
    recipient_contact: Optional[str] = None   # email or phone — masked in logs
    message:           str
    reference_id:      Optional[str] = None


class NotificationOut(BaseModel):
    notification_id:   int
    notification_type: str
    channel:           str
    recipient_id:      Optional[int]
    message:           str
    status:            str
    reference_id:      Optional[str]
    created_at:        datetime

    class Config:
        from_attributes = True


class Paginated(BaseModel):
    data:  List[NotificationOut]
    total: int
    page:  int
    size:  int

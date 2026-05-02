"""Background TTL reaper — releases expired inventory reservations."""
import logging

from app.models import Inventory, InventoryMovement, Reservation
from app.services.inventory_service import EXPIRY_RELEASES

logger = logging.getLogger("inventory.reaper")


def reaper_job(db_factory):
    from datetime import datetime
    db = db_factory()
    try:
        now     = datetime.utcnow()
        expired = db.query(Reservation).filter(
            Reservation.expires_at  <= now,
            Reservation.is_released == False,
            Reservation.is_shipped  == False,
        ).all()
        if not expired:
            return
        for res in expired:
            inv = db.query(Inventory).filter(
                Inventory.product_id == res.product_id,
                Inventory.warehouse  == res.warehouse,
            ).first()
            if inv:
                inv.reserved   = max(0, inv.reserved - res.quantity)
                inv.updated_at = now
            res.is_released = True
            db.add(InventoryMovement(
                product_id=res.product_id, warehouse=res.warehouse,
                order_id=res.order_id, type="RELEASE", quantity=res.quantity,
            ))
        db.commit()
        EXPIRY_RELEASES.inc(len(expired))
        logger.info("TTL reaper released %d expired reservations", len(expired))
    except Exception as exc:
        logger.error("Reaper error: %s", exc)
    finally:
        db.close()

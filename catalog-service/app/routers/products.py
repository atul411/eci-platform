from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import Paginated, ProductCreate, ProductOut, ProductUpdate
import app.services.product_service as svc

router = APIRouter(prefix="/v1/products", tags=["Products"])


@router.get("", response_model=Paginated)
def list_products(
    page:      int            = Query(1, ge=1),
    size:      int            = Query(20, ge=1, le=100),
    search:    Optional[str]  = None,
    category:  Optional[str]  = None,
    is_active: Optional[bool] = None,
    min_price: Optional[float]= None,
    max_price: Optional[float]= None,
    db: Session = Depends(get_db),
):
    return svc.list_products(db, page, size, search, category, is_active, min_price, max_price)


@router.post("", response_model=ProductOut, status_code=201)
def create_product(body: ProductCreate, db: Session = Depends(get_db)):
    return svc.create_product(db, body)


@router.get("/sku/{sku}", response_model=ProductOut)
def get_by_sku(sku: str, db: Session = Depends(get_db)):
    return svc.get_by_sku(db, sku)


@router.get("/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    return svc.get_product(db, product_id)


@router.put("/{product_id}", response_model=ProductOut)
def update_product(product_id: int, body: ProductUpdate, db: Session = Depends(get_db)):
    return svc.update_product(db, product_id, body)


@router.delete("/{product_id}")
def deactivate_product(product_id: int, db: Session = Depends(get_db)):
    return svc.deactivate_product(db, product_id)

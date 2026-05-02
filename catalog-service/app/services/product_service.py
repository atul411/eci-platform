import logging
import os

import pandas as pd
from fastapi import HTTPException
from prometheus_client import Counter
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Product
from app.schemas import ProductCreate, ProductUpdate

logger = logging.getLogger("catalog")

PRODUCTS_CREATED = Counter("catalog_products_created_total", "Products created")
PRODUCTS_FETCHED = Counter("catalog_products_fetched_total", "Product list requests")


def seed(db_factory):
    db = db_factory()
    try:
        if db.query(Product).count() > 0:
            return
        if not os.path.exists(settings.PRODUCTS_CSV):
            logger.warning("CSV not found: %s", settings.PRODUCTS_CSV)
            return
        df = pd.read_csv(settings.PRODUCTS_CSV)
        for _, r in df.iterrows():
            db.merge(Product(
                product_id=int(r["product_id"]),
                sku=str(r["sku"]),
                name=str(r["name"]),
                category=str(r["category"]),
                price=float(r["price"]),
                is_active=str(r["is_active"]).strip().lower() == "true",
            ))
        db.commit()
        logger.info("Seeded %d products", len(df))
    except Exception as exc:
        logger.error("Seed error: %s", exc)
    finally:
        db.close()


def list_products(db: Session, page: int, size: int,
                  search=None, category=None, is_active=None,
                  min_price=None, max_price=None) -> dict:
    PRODUCTS_FETCHED.inc()
    q = db.query(Product)
    if search    is not None: q = q.filter(Product.name.ilike(f"%{search}%"))
    if category  is not None: q = q.filter(Product.category == category)
    if is_active is not None: q = q.filter(Product.is_active == is_active)
    if min_price is not None: q = q.filter(Product.price >= min_price)
    if max_price is not None: q = q.filter(Product.price <= max_price)
    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return {"data": items, "total": total, "page": page, "size": size}


def create_product(db: Session, body: ProductCreate) -> Product:
    if db.query(Product).filter(Product.sku == body.sku).first():
        raise HTTPException(409, f"SKU {body.sku} already exists")
    p = Product(**body.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    PRODUCTS_CREATED.inc()
    logger.info("Product created: id=%d sku=%s", p.product_id, p.sku)
    return p


def get_by_sku(db: Session, sku: str) -> Product:
    p = db.query(Product).filter(Product.sku == sku).first()
    if not p:
        raise HTTPException(404, f"SKU '{sku}' not found")
    return p


def get_product(db: Session, product_id: int) -> Product:
    p = db.query(Product).filter(Product.product_id == product_id).first()
    if not p:
        raise HTTPException(404, f"Product {product_id} not found")
    return p


def update_product(db: Session, product_id: int, body: ProductUpdate) -> Product:
    p = get_product(db, product_id)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    logger.info("Product updated: id=%d", product_id)
    return p


def deactivate_product(db: Session, product_id: int) -> dict:
    p = get_product(db, product_id)
    p.is_active = False
    db.commit()
    logger.info("Product deactivated: id=%d", product_id)
    return {"message": "Product deactivated", "product_id": product_id}

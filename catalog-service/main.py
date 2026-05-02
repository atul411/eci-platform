"""Catalog Service — manages product catalog with CRUD, search, and pricing."""
import hashlib
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pythonjsonlogger import jsonlogger
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker
import logging

# ── Logging ──────────────────────────────────────────────────────────────────
_logger = logging.getLogger("catalog")
_handler = logging.StreamHandler()
_handler.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
_logger.addHandler(_handler)
_logger.setLevel(logging.INFO)


def _mask(email: str) -> str:
    if not email or "@" not in email:
        return "***"
    u, d = email.split("@", 1)
    return u[:2] + "***@" + d


def log(msg: str, level: str = "info", **ctx):
    ctx["service"] = "catalog-service"
    getattr(_logger, level)(msg, extra=ctx)


# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./catalog.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Product(Base):
    __tablename__ = "products"
    product_id = Column(Integer, primary_key=True, index=True)
    sku        = Column(String(50), unique=True, index=True, nullable=False)
    name       = Column(String(200), nullable=False)
    category   = Column(String(100))
    price      = Column(Float, nullable=False)
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Metrics ───────────────────────────────────────────────────────────────────
PRODUCTS_CREATED = Counter("catalog_products_created_total", "Products created")
PRODUCTS_FETCHED = Counter("catalog_products_fetched_total", "Product list requests")
REQUEST_LATENCY  = Histogram("catalog_request_latency_seconds", "Latency", ["endpoint"])


# ── Schemas ───────────────────────────────────────────────────────────────────
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


# ── Seed ──────────────────────────────────────────────────────────────────────
def seed():
    db = SessionLocal()
    try:
        if db.query(Product).count() > 0:
            return
        csv_path = os.getenv("PRODUCTS_CSV", "/data/eci_products_indian.csv")
        if not os.path.exists(csv_path):
            log(f"CSV not found: {csv_path}", level="warning")
            return
        df = pd.read_csv(csv_path)
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
        log("Products seeded", count=len(df))
    except Exception as exc:
        log(f"Seed error: {exc}", level="error")
    finally:
        db.close()


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed()
    yield


app = FastAPI(title="Catalog Service", version="1.0.0", lifespan=lifespan,
              description="Manages product catalog with CRUD and search.")


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    cid = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.cid = cid
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = cid
    return response


@app.exception_handler(HTTPException)
async def http_exc(request: Request, exc: HTTPException):
    return JSONResponse(exc.status_code, {"error": {
        "code": str(exc.status_code),
        "message": exc.detail,
        "correlationId": getattr(request.state, "cid", str(uuid.uuid4())),
    }})


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "healthy", "service": "catalog-service", "timestamp": datetime.utcnow().isoformat()}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/products", response_model=Paginated)
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
    PRODUCTS_FETCHED.inc()
    q = db.query(Product)
    if search:              q = q.filter(Product.name.ilike(f"%{search}%"))
    if category:            q = q.filter(Product.category == category)
    if is_active is not None: q = q.filter(Product.is_active == is_active)
    if min_price is not None: q = q.filter(Product.price >= min_price)
    if max_price is not None: q = q.filter(Product.price <= max_price)
    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()
    return {"data": items, "total": total, "page": page, "size": size}


@app.post("/v1/products", response_model=ProductOut, status_code=201)
def create_product(body: ProductCreate, db: Session = Depends(get_db)):
    if db.query(Product).filter(Product.sku == body.sku).first():
        raise HTTPException(409, f"SKU {body.sku} already exists")
    p = Product(**body.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    PRODUCTS_CREATED.inc()
    log("Product created", product_id=p.product_id, sku=p.sku)
    return p


# NOTE: /sku/{sku} MUST be defined before /{product_id} so FastAPI doesn't
# attempt to parse the literal "sku" as an integer.
@app.get("/v1/products/sku/{sku}", response_model=ProductOut)
def get_by_sku(sku: str, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.sku == sku).first()
    if not p:
        raise HTTPException(404, f"SKU '{sku}' not found")
    return p


@app.get("/v1/products/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.product_id == product_id).first()
    if not p:
        raise HTTPException(404, f"Product {product_id} not found")
    return p


@app.put("/v1/products/{product_id}", response_model=ProductOut)
def update_product(product_id: int, body: ProductUpdate, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.product_id == product_id).first()
    if not p:
        raise HTTPException(404, f"Product {product_id} not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    log("Product updated", product_id=product_id)
    return p


@app.delete("/v1/products/{product_id}")
def deactivate_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.product_id == product_id).first()
    if not p:
        raise HTTPException(404, f"Product {product_id} not found")
    p.is_active = False
    db.commit()
    log("Product deactivated", product_id=product_id)
    return {"message": "Product deactivated", "product_id": product_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8001)))

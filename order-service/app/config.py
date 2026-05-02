import os
from decimal import Decimal


class Settings:
    DATABASE_URL:     str = os.getenv("DATABASE_URL",             "sqlite:///./order.db")
    ORDERS_CSV:       str = os.getenv("ORDERS_CSV",               "/data/eci_orders_indian.csv")
    ORDER_ITEMS_CSV:  str = os.getenv("ORDER_ITEMS_CSV",          "/data/eci_order_items_indian.csv")
    CATALOG_URL:      str = os.getenv("CATALOG_SERVICE_URL",      "http://catalog-service:8001")
    INVENTORY_URL:    str = os.getenv("INVENTORY_SERVICE_URL",    "http://inventory-service:8002")
    PAYMENT_URL:      str = os.getenv("PAYMENT_SERVICE_URL",      "http://payment-service:8004")
    SHIPPING_URL:     str = os.getenv("SHIPPING_SERVICE_URL",     "http://shipping-service:8005")
    NOTIFICATION_URL: str = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8006")
    SHIPPING_CHARGE:  Decimal = Decimal("100.00")
    TAX_RATE:         Decimal = Decimal("0.05")


settings = Settings()

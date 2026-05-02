import os


class Settings:
    DATABASE_URL:           str = os.getenv("DATABASE_URL",             "sqlite:///./inventory.db")
    INVENTORY_CSV:          str = os.getenv("INVENTORY_CSV",            "/data/eci_inventory_indian.csv")
    NOTIFICATION_URL:       str = os.getenv("NOTIFICATION_SERVICE_URL", "http://notification-service:8006")
    LOW_STOCK_THRESHOLD:    int = int(os.getenv("LOW_STOCK_THRESHOLD",  "10"))
    RESERVATION_TTL_MINS:   int = int(os.getenv("RESERVATION_TTL_MINUTES", "15"))


settings = Settings()

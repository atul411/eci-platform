import os


class Settings:
    DATABASE_URL:  str = os.getenv("DATABASE_URL",   "sqlite:///./shipping.db")
    SHIPMENTS_CSV: str = os.getenv("SHIPMENTS_CSV",  "/data/eci_shipments_indian.csv")


settings = Settings()

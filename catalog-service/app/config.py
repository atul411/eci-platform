import os


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./catalog.db")
    PRODUCTS_CSV: str = os.getenv("PRODUCTS_CSV", "/data/eci_products_indian.csv")


settings = Settings()

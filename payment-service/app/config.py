import os


class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./payment.db")
    PAYMENTS_CSV: str = os.getenv("PAYMENTS_CSV", "/data/eci_payments_indian.csv")


settings = Settings()

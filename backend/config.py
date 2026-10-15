import os
from pydantic_settings import BaseSettings
from pydantic import ConfigDict
from typing import Optional

class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")

    PROJECT_NAME: str = "Concord Inventory Reservation Service"
    ENVIRONMENT: str = "development"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # MongoDB
    MONGO_URI: str = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "concord")

    # Redis
    REDIS_URI: str = os.getenv("REDIS_URI", "redis://localhost:6379/0")

    # Business Rules
    DEFAULT_HOLD_TTL_SECONDS: int = int(os.getenv("DEFAULT_HOLD_TTL_SECONDS", "30"))
    EXPIRATION_WORKER_INTERVAL_SECONDS: float = float(os.getenv("EXPIRATION_WORKER_INTERVAL_SECONDS", "2.0"))
    RECONCILIATION_WORKER_INTERVAL_SECONDS: float = float(os.getenv("RECONCILIATION_WORKER_INTERVAL_SECONDS", "5.0"))
    MAX_RESERVATION_QUANTITY: int = int(os.getenv("MAX_RESERVATION_QUANTITY", "100"))
    STREAM_MAX_LEN: int = int(os.getenv("STREAM_MAX_LEN", "10000"))
    STREAM_KEY: str = "concord:events"

settings = Settings()

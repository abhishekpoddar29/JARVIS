from __future__ import annotations

import os

from .service import TrackingService
from .store import TrackingStore

TRACKING_REDIS_URL = os.getenv(
    "TRACKING_REDIS_URL",
    "redis://127.0.0.1:6379/1",
)

tracking_store = TrackingStore(TRACKING_REDIS_URL)
tracking_service = TrackingService(
    store=tracking_store,
    public_base_url=os.getenv(
        "TRACKING_PUBLIC_BASE_URL", "http://127.0.0.1:8000"
    ),
)

"""各來源類型的 Adapter；匯入本套件即向 `app.ingestion.registry.registry` 註冊全部類型。"""

from app.ingestion.adapters import (
    anonymous_report,
    cwa_open_data,
    manual_import,
    ncu_announcements,
    ncu_epsc,
    ncu_lib,
    ncu_news,
    ncu_osa_life,
    rss,
)

__all__ = [
    "anonymous_report",
    "cwa_open_data",
    "manual_import",
    "ncu_announcements",
    "ncu_epsc",
    "ncu_lib",
    "ncu_news",
    "ncu_osa_life",
    "rss",
]

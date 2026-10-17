"""各來源類型的 Adapter；匯入本套件即向 `app.ingestion.registry.registry` 註冊全部類型。"""

from app.ingestion.adapters import ncu_announcements, rss

__all__ = ["ncu_announcements", "rss"]

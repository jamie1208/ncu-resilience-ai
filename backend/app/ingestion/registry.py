"""來源類型與 Adapter 的對應表。

只有核准（access_policy = approved）且啟用的來源可以建立 Adapter，
對應 P0-T09 的來源授權盤點與閘門 G1-1。
"""

from __future__ import annotations

from collections.abc import Callable

from app.ingestion.base import SourceAdapter
from app.ingestion.types import AccessPolicy, SourceConfig

AdapterFactory = Callable[[SourceConfig], SourceAdapter]


class UnknownSourceTypeError(LookupError):
    """沒有對應此來源類型的 Adapter。"""


class DuplicateSourceTypeError(ValueError):
    """同一來源類型重複註冊。"""


class SourceNotApprovedError(PermissionError):
    """來源未核准或未啟用，不得擷取。"""


class SourceRegistry:
    """來源類型 → Adapter 工廠。"""

    def __init__(self) -> None:
        self._factories: dict[str, AdapterFactory] = {}

    def register[F: AdapterFactory](self, source_type: str) -> Callable[[F], F]:
        """裝飾器：把 Adapter 類別或工廠註冊為 `source_type` 的實作。"""

        def decorator(factory: F) -> F:
            if source_type in self._factories:
                raise DuplicateSourceTypeError(f"來源類型 {source_type} 已註冊")
            self._factories[source_type] = factory
            return factory

        return decorator

    def types(self) -> frozenset[str]:
        """已註冊的來源類型。"""
        return frozenset(self._factories)

    def create(self, config: SourceConfig) -> SourceAdapter:
        """為已核准且啟用的來源建立 Adapter。"""
        if config.access_policy is not AccessPolicy.APPROVED or not config.enabled:
            raise SourceNotApprovedError(
                f"來源 {config.code} 未核准或未啟用"
                f"（access_policy={config.access_policy}, enabled={config.enabled}）"
            )
        factory = self._factories.get(config.type)
        if factory is None:
            raise UnknownSourceTypeError(
                f"來源 {config.code} 的類型 {config.type} 沒有對應的 Adapter"
            )
        return factory(config)


registry = SourceRegistry()
"""應用程式共用的 registry；各 Adapter 模組以 `@registry.register("<type>")` 註冊。"""

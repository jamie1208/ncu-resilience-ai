"""P5-T06：本機常駐運行的 compose 覆寫檔（restart 策略、所有埠號只綁定 127.0.0.1）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
BASE = REPO / "infra" / "docker" / "docker-compose.yml"
LOCAL = REPO / "infra" / "docker" / "docker-compose.local.yml"
LOOPBACK_PORT = re.compile(r"^127\.0\.0\.1:\$\{[A-Z_]+:-\d+\}:\d+$")


class OverrideList(list[Any]):
    """標記為 compose `!override` 的清單：取代基礎檔的值，而不是合併。"""


class ComposeLoader(yaml.SafeLoader):
    """認得 compose 的 `!override`／`!reset` 標籤的 YAML loader。"""


def _override(loader: ComposeLoader, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return OverrideList(loader.construct_sequence(node))
    raise AssertionError("本測試只預期清單使用 !override")


def _reset(loader: ComposeLoader, node: yaml.Node) -> None:
    raise AssertionError("常駐設定不應以 !reset 移除基礎檔的設定")


ComposeLoader.add_constructor("!override", _override)
ComposeLoader.add_constructor("!reset", _reset)


def load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.load(path.read_text(encoding="utf-8"), Loader=ComposeLoader)  # noqa: S506
    return data


BASE_SERVICES: dict[str, Any] = load(BASE)["services"]
LOCAL_SERVICES: dict[str, Any] = load(LOCAL)["services"]


def test_local_override_covers_every_service() -> None:
    assert set(LOCAL_SERVICES) == set(BASE_SERVICES)


@pytest.mark.parametrize("service", sorted(BASE_SERVICES))
def test_service_restarts_automatically(service: str) -> None:
    assert LOCAL_SERVICES[service].get("restart") == "unless-stopped"


@pytest.mark.parametrize("service", sorted(BASE_SERVICES))
def test_published_ports_replace_base_and_bind_loopback(service: str) -> None:
    base_ports = BASE_SERVICES[service].get("ports")
    local_ports = LOCAL_SERVICES[service].get("ports")
    if base_ports is None:
        assert local_ports is None, f"{service} 在基礎檔沒有發布埠號，常駐模式也不應發布"
        return
    # compose 預設會合併 ports；用 !override 讓常駐模式的綁定成為唯一的來源
    assert isinstance(local_ports, OverrideList), f"{service} 的 ports 應以 !override 取代"
    assert local_ports, f"{service} 的 ports 不應為空"
    for port in local_ports:
        assert LOOPBACK_PORT.match(port), f"{service} 的 {port} 未只綁定 127.0.0.1"
    # 主機埠號與容器埠號與基礎檔一致，不改既有的連線方式
    assert sorted(local_ports) == sorted(base_ports)


@pytest.mark.parametrize("service", sorted(BASE_SERVICES))
def test_container_logs_are_bounded(service: str) -> None:
    logging = LOCAL_SERVICES[service].get("logging")
    assert logging is not None, f"{service} 長期運行需限制日誌大小"
    assert logging["driver"] == "json-file"
    assert logging["options"]["max-size"]
    assert int(logging["options"]["max-file"]) >= 1

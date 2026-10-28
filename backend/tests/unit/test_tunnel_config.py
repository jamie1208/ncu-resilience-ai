"""P5-T15：通道對外設定（只轉送公開網站與 /api/v1/public/*，管理端與登入不經通道）。

實際的路徑放行／拒絕由 `infra/tunnel/tunnel.sh check` 對運行中的閘道驗證；
本檔檢查設定檔本身的不變條件，避免日後改動悄悄打開管理端。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
TUNNEL_DIR = REPO / "infra" / "tunnel"
COMPOSE = TUNNEL_DIR / "docker-compose.tunnel.yml"
GATEWAY_CONF = TUNNEL_DIR / "nginx-public.conf"
LOOPBACK_PORT = re.compile(r"^127\.0\.0\.1:\$\{[A-Z_]+:-\d+\}:\d+$")


def _services() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services: dict[str, Any] = data["services"]
    return services


def _conf() -> str:
    # 去掉註解，避免註解裡的字樣影響判斷
    lines = GATEWAY_CONF.read_text(encoding="utf-8").splitlines()
    return "\n".join(line.split("#", 1)[0] for line in lines)


def _locations() -> dict[str, str]:
    """location 修飾與路徑 → 區塊內容（本設定的 location 不巢狀）。"""
    blocks: dict[str, str] = {}
    for match in re.finditer(r"location\s+([^{]+?)\s*\{(.*?)\n\s*\}", _conf(), re.S):
        blocks[match.group(1).strip()] = match.group(2)
    return blocks


def test_overlay_only_touches_gateway_tunnel_and_api_env() -> None:
    services = _services()
    assert set(services) == {"public-gateway", "tunnel", "api"}
    # api 只加上信任閘道轉送標頭的設定，不改埠號、指令或映像
    assert set(services["api"]) == {"environment"}
    assert services["api"]["environment"]["FORWARDED_ALLOW_IPS"]


def test_tunnel_points_only_at_gateway_and_is_opt_in() -> None:
    tunnel = _services()["tunnel"]
    # 需明確 --profile tunnel 才會啟動：平常的 up 不會意外對外
    assert tunnel["profiles"] == ["tunnel"]
    image = tunnel["image"]
    assert image.startswith("cloudflare/cloudflared:")
    assert not image.endswith(":latest")
    command = tunnel["command"]
    assert command[:2] == ["tunnel", "--no-autoupdate"]
    url = command[command.index("--url") + 1]
    assert url == "http://public-gateway:8080"
    # 不發布埠號、不掛載任何憑證或設定（Quick Tunnel 不需要帳號）
    assert "ports" not in tunnel
    assert "volumes" not in tunnel
    assert "public-gateway" in tunnel["depends_on"]


def test_gateway_binds_loopback_and_mounts_config_read_only() -> None:
    gateway = _services()["public-gateway"]
    assert gateway["image"].startswith("nginx:")
    assert gateway["restart"] == "unless-stopped"
    for port in gateway.get("ports", []):
        assert LOOPBACK_PORT.match(port), f"閘道的 {port} 未只綁定 127.0.0.1"
    assert any(
        v.endswith(":/etc/nginx/conf.d/default.conf:ro") and "nginx-public.conf" in v
        for v in gateway["volumes"]
    )


def test_gateway_denies_everything_not_allowlisted() -> None:
    catch_all = _locations()["/"]
    assert re.search(r"return\s+404\s*;", catch_all)
    assert "proxy_pass" not in catch_all


def test_api_location_only_matches_public_prefix() -> None:
    api_blocks = {k: v for k, v in _locations().items() if "api" in k}
    assert api_blocks, "應有轉送公開 API 的 location"
    for key in api_blocks:
        assert "/api/v1/public/" in key, f"{key} 不應轉送公開以外的 API"


def test_no_location_forwards_admin_or_auth() -> None:
    for key, body in _locations().items():
        if "proxy_pass" not in body:
            continue
        for forbidden in ("admin", "auth", "login", "health", "metrics"):
            assert forbidden not in key, f"{key} 轉送了不應對外的路徑"


def test_gateway_does_not_log_or_forward_identifying_headers() -> None:
    conf = _conf()
    # 匿名投稿者不可被追蹤：閘道不記存取日誌，也不把 Cookie、UA、Referer 往內送
    assert re.search(r"^\s*access_log\s+off\s*;", conf, re.M)
    for header in ("Cookie", "User-Agent", "Referer"):
        assert re.search(rf'proxy_set_header\s+{header}\s+""\s*;', conf), header
    # 限速識別用通道提供的用戶端位址，並覆寫（不附加）使用者自帶的 X-Forwarded-For
    assert re.search(r"proxy_set_header\s+X-Forwarded-For\s+\$ncu_client\s*;", conf)
    assert "$http_cf_connecting_ip" in conf
    assert "$proxy_add_x_forwarded_for" not in conf


def test_public_api_is_not_cached_at_the_edge() -> None:
    api_body = next(v for k, v in _locations().items() if "/api/v1/public/" in k)
    # 下架需即時生效（G5-7）：通道邊緣不快取公開 API
    assert re.search(r"add_header\s+Cloudflare-CDN-Cache-Control\s+no-store\s+always\s*;", api_body)

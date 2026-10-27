"""P4-T07：Facebook 每日擷取的網路等待與重試（`scripts/facebook/run_capture.sh`）。

以假的 claude 執行檔與 file:// 的連線檢查網址測試，不連網、不執行真正的 claude。
- 網路可連線後才執行；等不到網路時不執行並失敗。
- 輸出 `API Error` 或結束碼非 0 時重試，次數有上限。
- 需要使用者處理（Chrome 擴充功能未連線）時不重試。
- launchd 範本經包裝腳本執行，claude 參數原樣轉交。
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "facebook" / "run_capture.sh"
PLIST = REPO / "infra" / "launchd" / "tw.ncu-resilience.fb-capture.plist.template"

FAKE_CLAUDE = """#!/usr/bin/env bash
# 依序取出 responses 的一行：<結束碼> <輸出>；每次呼叫記一行參數
echo "$*" >> "{calls}"
line=$(sed -n "$(( $(wc -l < "{calls}") ))p" "{responses}")
echo "${{line#* }}"
exit "${{line%% *}}"
"""


def run(
    tmp_path: Path, responses: list[str], *, network: bool = True, tries: int = 3
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    calls = tmp_path / "calls.txt"
    calls.touch()
    (tmp_path / "responses.txt").write_text("\n".join(responses) + "\n", encoding="utf-8")
    claude = tmp_path / "claude"
    claude.write_text(
        FAKE_CLAUDE.format(calls=calls, responses=tmp_path / "responses.txt"), encoding="utf-8"
    )
    claude.chmod(0o755)
    probe = tmp_path / "probe"
    if network:
        probe.write_text("ok", encoding="utf-8")
    env = {
        **os.environ,
        "FB_CAPTURE_PROBE_URL": probe.as_uri(),
        "FB_CAPTURE_TRIES": str(tries),
        "FB_CAPTURE_RETRY_SECONDS": "0",
        "FB_CAPTURE_NET_WAIT_SECONDS": "1",
        "FB_CAPTURE_NET_POLL_SECONDS": "1",
    }
    result = subprocess.run(  # noqa: S603 — 只執行 repo 內的腳本與測試建立的假 claude
        ["/bin/bash", str(SCRIPT), str(claude), "-p", "--", "/ncu-fb-capture"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=False,
    )
    return result, calls.read_text(encoding="utf-8").splitlines()


def test_success_runs_once_and_forwards_arguments(tmp_path: Path) -> None:
    result, calls = run(tmp_path, ["0 完成：2026-10-27-0800.json，12 篇"])
    assert result.returncode == 0
    assert calls == ["-p -- /ncu-fb-capture"]
    assert "完成" in result.stdout


def test_api_error_is_retried_until_success(tmp_path: Path) -> None:
    result, calls = run(
        tmp_path,
        [
            "1 API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)",
            "0 API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)",
            "0 完成：2026-10-27-0820.json，12 篇",
        ],
    )
    assert result.returncode == 0
    assert len(calls) == 3


def test_gives_up_after_max_tries(tmp_path: Path) -> None:
    result, calls = run(tmp_path, ["1 API Error: x"] * 5, tries=2)
    assert result.returncode != 0
    assert len(calls) == 2


def test_user_action_needed_is_not_retried(tmp_path: Path) -> None:
    result, calls = run(tmp_path, ["0 需要使用者處理：Claude in Chrome 擴充功能未連線。"])
    assert result.returncode != 0
    assert len(calls) == 1


def test_does_not_run_without_network(tmp_path: Path) -> None:
    result, calls = run(tmp_path, ["0 完成"], network=False)
    assert result.returncode != 0
    assert calls == []


def test_plist_runs_capture_through_wrapper() -> None:
    raw = PLIST.read_text(encoding="utf-8")
    plist = plistlib.loads(
        raw.replace("__REPO__", "/Users/demo/ncu-resilience-ai")
        .replace("__CLAUDE__", "/Users/demo/.local/bin/claude")
        .encode()
    )
    args = plist["ProgramArguments"]
    assert args[:3] == [
        "/bin/bash",
        "/Users/demo/ncu-resilience-ai/scripts/facebook/run_capture.sh",
        "/Users/demo/.local/bin/claude",
    ]
    assert args[3] == "-p"

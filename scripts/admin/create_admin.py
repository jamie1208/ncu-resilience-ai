"""建立唯一的使用者，或重設其密碼（P3-T01；ADR-0008：單一使用者）。

在 backend/ 執行（需先 make up 並跑完 migration）：
    uv run python ../scripts/admin/create_admin.py --username <帳號>
密碼以互動方式輸入兩次（不回顯）；非互動環境可改用環境變數 NCU_ADMIN_PASSWORD。
重設密碼會解除鎖定並撤銷所有已登入的 session。邏輯在 backend/app/services/auth.py。
"""

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path


def read_password() -> str:
    """從環境變數或互動輸入取得密碼；兩次輸入不一致時結束。"""
    env = os.environ.get("NCU_ADMIN_PASSWORD")
    if env:
        return env
    first = getpass.getpass("密碼：")
    second = getpass.getpass("再輸入一次：")
    if first != second:
        raise SystemExit("兩次輸入的密碼不一致")
    return first


def main() -> int:
    """把 backend/ 加入匯入路徑後執行；失敗時回傳 1。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from app.db.session import get_engine, get_sessionmaker
    from app.services.auth import SingleUserError, create_or_reset_admin

    parser = argparse.ArgumentParser(description="建立或重設唯一的使用者")
    parser.add_argument("--username", required=True, help="登入帳號（不要用 email 或真實姓名）")
    args = parser.parse_args()
    password = read_password()

    async def run() -> str:
        try:
            async with get_sessionmaker()() as session:
                user = await create_or_reset_admin(session, args.username, password)
                await session.commit()
                return str(user.id)
        finally:
            await get_engine().dispose()

    try:
        user_id = asyncio.run(run())
    except (SingleUserError, ValueError) as e:
        print(f"失敗：{e}", file=sys.stderr)
        return 1
    print(f"完成：使用者 {args.username}（{user_id}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

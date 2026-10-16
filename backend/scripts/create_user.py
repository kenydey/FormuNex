#!/usr/bin/env python3
"""Create a FormuNex account from the command line — the way to make the first administrator.

The password is read from the terminal (or ``FORMUNEX_NEW_USER_PASSWORD`` for automation), never
from an argument, so it does not land in shell history or the process list.

用法:
    .venv/bin/python scripts/create_user.py admin --roles sys_admin --display-name 管理员
    .venv/bin/python scripts/create_user.py zhang.san --roles project_lead,rd_engineer
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("username")
    parser.add_argument("--roles", default="", help="Comma-separated role codes (see GET /api/identity/roles)")
    parser.add_argument("--display-name", default="")
    parser.add_argument("--email")
    parser.add_argument("--db-url", help="SQLAlchemy URL (default: FORMUNEX_DB_URL / settings.db_url)")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from app.services.identity import accounts
    from app.services.identity.rbac import sync_builtin_roles

    password = os.environ.get("FORMUNEX_NEW_USER_PASSWORD")
    if not password:
        password = getpass.getpass("密码: ")
        if password != getpass.getpass("确认密码: "):
            print("两次输入的密码不一致", file=sys.stderr)
            return 2
    engine = create_engine(args.db_url or get_settings().db_url, future=True)
    try:
        with Session(engine) as session:
            sync_builtin_roles(session)
            session.flush()
            try:
                user = accounts.create_user(
                    session, username=args.username, password=password, display_name=args.display_name,
                    email=args.email, roles=[r.strip() for r in args.roles.split(",") if r.strip()], actor="cli")
            except accounts.AccountError as exc:
                print(f"创建失败：{exc}", file=sys.stderr)
                return 1
            session.commit()
            print(f"已创建用户 {user.username}（{user.id}）")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

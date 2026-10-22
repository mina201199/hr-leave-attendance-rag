import argparse
import os
import sqlite3
import sys
from getpass import getpass
from pathlib import Path


projectPath = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(projectPath / "src"))

from dotenv import dotenv_values

from labor_rag.auth import AuthenticationError, AuthStore, ROLES


def main(argv=None):
  parser = argparse.ArgumentParser(description="本機企業帳號管理；密碼僅透過隱藏互動輸入")
  parser.add_argument("command", choices=("create", "set-role", "set-password", "disable", "enable"))
  parser.add_argument("--user", required=True, help="個人帳號識別碼")
  parser.add_argument("--role", choices=ROLES)
  parser.add_argument("--manage", action="store_true", help="授予 hr 帳號 knowledge:manage")
  parser.add_argument("--data-dir", type=Path, help="企業資料目錄（預設 RAG_ENTERPRISE_DATA_DIR 或 data/index/enterprise）")
  arguments, extraArgs = parser.parse_known_args(argv)
  if extraArgs:
    parser.error("不支援的參數；密碼只能透過互動輸入")
  changesRole = arguments.command in ("create", "set-role")
  if changesRole and arguments.role is None:
    parser.error("create 與 set-role 必須提供 --role")
  if not changesRole and (arguments.role is not None or arguments.manage):
    parser.error("--role 與 --manage 僅適用於 create 或 set-role")
  if arguments.manage and arguments.role != "hr":
    parser.error("只有 hr 可以授予 --manage")
  try:
    password = None
    if arguments.command in ("create", "set-password"):
      password = getpass("請輸入密碼（12 至 256 字元）：")
      if password != getpass("請再次輸入密碼："):
        raise ValueError("兩次密碼輸入不一致")
    values = {**dotenv_values(projectPath / ".env"), **os.environ}
    dataDir = Path(arguments.data_dir or values.get("RAG_ENTERPRISE_DATA_DIR") or "data/index/enterprise")
    if not dataDir.is_absolute():
      dataDir = projectPath / dataDir
    store = AuthStore(dataDir / "auth.db")
    capabilities = ("knowledge:manage",) if arguments.manage else ()
    if arguments.command == "create":
      store.createUser(arguments.user, password, arguments.role, capabilities)
    elif arguments.command == "set-role":
      store.setRole(arguments.user, arguments.role, capabilities)
    elif arguments.command == "set-password":
      store.setPassword(arguments.user, password)
    else:
      store.setEnabled(arguments.user, arguments.command == "enable")
    print("已更新個人帳號；帳號變更會撤銷既有登入。")
  except (AuthenticationError, ValueError, OSError, sqlite3.DatabaseError) as error:
    parser.exit(1, f"操作失敗：{error}\n")
  except (EOFError, KeyboardInterrupt):
    parser.exit(1, "操作已取消。\n")


if __name__ == "__main__":
  main()

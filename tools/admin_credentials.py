from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from app.config import SecretStore
from app.database import Database
from app.security import AuthManager


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or reset the Whackamole UI administrator offline.")
    parser.add_argument("--config-dir", default=os.getenv("WHACKAMOLE_CONFIG_DIR", "/config"))
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-env", default="WHACKAMOLE_ADMIN_PASSWORD")
    args = parser.parse_args()

    password = os.getenv(args.password_env, "") or getpass.getpass("New administrator password: ")
    confirmation = os.getenv(args.password_env, "") or getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Password confirmation does not match")

    config_dir = Path(args.config_dir)
    db = Database(str(config_dir / "whackamole.db"))
    auth = AuthManager(db, SecretStore(str(config_dir)))
    if auth.has_admin():
        auth.update_admin(args.username, password)
        action = "reset"
    else:
        auth.create_admin(args.username, password)
        action = "created"
    print(f"Administrator credentials {action}; all browser sessions revoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Install Mythic+ (MVP) schema/content into TrinityCore-style databases.

This installer uses the `mysql` CLI (same approach as wow_randomizer.py) to avoid
additional Python DB dependencies.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    mysql_binary: str = "mysql"


class MysqlClient:
    def __init__(self, config: DbConfig) -> None:
        self.config = config

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.config.password:
            env["MYSQL_PWD"] = self.config.password
        else:
            env.pop("MYSQL_PWD", None)
        return env

    def _base_command(self) -> list[str]:
        return [
            self.config.mysql_binary,
            "-h",
            self.config.host,
            "-P",
            str(self.config.port),
            "-u",
            self.config.user,
            "-D",
            self.config.database,
        ]

    def run_sql(self, sql_text: str) -> None:
        completed = subprocess.run(
            self._base_command(),
            text=True,
            input=sql_text,
            capture_output=True,
            env=self._env(),
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"MySQL script failed for DB '{self.config.database}' "
                f"(code {completed.returncode}): "
                f"{completed.stderr.strip() or 'unknown error'}"
            )


def read_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Mythic+ (MVP) SQL into DBs.")

    parser.add_argument("--host", default=os.getenv("WOW_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("WOW_DB_PORT", "3306")))
    parser.add_argument("--user", default=os.getenv("WOW_DB_USER", "wow"))
    parser.add_argument("--password", default=os.getenv("WOW_DB_PASSWORD", "wow123"))
    parser.add_argument("--world-db", default=os.getenv("WOW_WORLD_DB", "world"))
    parser.add_argument("--characters-db", default=os.getenv("WOW_CHARACTERS_DB", "characters"))
    parser.add_argument("--mysql-binary", default=os.getenv("WOW_MYSQL_BINARY", "mysql"))

    parser.add_argument(
        "--apply",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Actually apply to DBs (default: dry-run prints what would run).",
    )
    parser.add_argument(
        "--install-content",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Install item_template/creature_template entries into WORLD DB.",
    )
    parser.add_argument(
        "--seed-wotlk-dungeons",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Seed a default WotLK 5p dungeon list into WORLD DB.",
    )
    parser.add_argument(
        "--seed-world-portals",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Seed default open-world hourly portal spawn points.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    sql_dir = repo_root / "sql"

    world_schema = sql_dir / "mythic_plus_world_schema.sql"
    chars_schema = sql_dir / "mythic_plus_characters_schema.sql"
    seed_wotlk = sql_dir / "mythic_plus_seed_wotlk_dungeons.sql"
    seed_world_portals = sql_dir / "mythic_plus_world_portal_spawn_seed.sql"
    world_content = sql_dir / "mythic_plus_world_content.sql"

    missing = [
        p
        for p in [world_schema, chars_schema, seed_wotlk, seed_world_portals, world_content]
        if not p.exists()
    ]
    if missing:
        raise RuntimeError(f"Missing SQL files: {', '.join(str(p) for p in missing)}")

    plan: list[tuple[str, str]] = []
    plan.append((args.world_db, read_sql(world_schema)))
    plan.append((args.characters_db, read_sql(chars_schema)))

    if args.seed_wotlk_dungeons:
        plan.append((args.world_db, read_sql(seed_wotlk)))

    if args.seed_world_portals:
        plan.append((args.world_db, read_sql(seed_world_portals)))

    if args.install_content:
        plan.append((args.world_db, read_sql(world_content)))

    if not args.apply:
        print("Dry-run mode. Would execute the following steps:")
        for db, sql in plan:
            print(f"\n--- DB: {db} ---")
            print(sql.strip())
        print("\nRe-run with --apply to execute.")
        return 0

    for db, sql in plan:
        client = MysqlClient(
            DbConfig(
                host=args.host,
                port=args.port,
                user=args.user,
                password=args.password,
                database=db,
                mysql_binary=args.mysql_binary,
            )
        )
        client.run_sql(sql)
        print(f"Applied to DB '{db}'.")

    print("Done.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)


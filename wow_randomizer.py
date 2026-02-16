#!/usr/bin/env python3
"""WoW 3.3.5a world randomizer with reroll support.

This tool is designed for private servers based on 3.3.5a world schema
(AzerothCore/TrinityCore compatible for the touched tables).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import random
import sys
import time
from collections import defaultdict
from typing import Dict, Iterable, List, Sequence, Tuple, TypeVar

import pymysql
import yaml


META_TABLE = "wr_randomizer_meta"
BACKUP_PREFIX = "wr_backup_"

TABLE_CREATURE = "creature"
TABLE_CREATURE_TEMPLATE = "creature_template"
TABLE_CREATURE_LOOT = "creature_loot_template"
TABLE_QUEST = "quest_template"
TABLE_ITEM = "item_template"


@dataclasses.dataclass
class DatabaseConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str = "utf8mb4"


@dataclasses.dataclass
class ModulesConfig:
    creatures: bool = True
    creature_stats: bool = True
    loot: bool = True
    quests: bool = True
    item_stats: bool = True


@dataclasses.dataclass
class RandomizerConfig:
    chunk_size: int = 1000


@dataclasses.dataclass
class AppConfig:
    database: DatabaseConfig
    modules: ModulesConfig
    randomizer: RandomizerConfig


def parse_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    db = data["database"]
    modules = data.get("modules", {})
    randomizer = data.get("randomizer", {})

    return AppConfig(
        database=DatabaseConfig(
            host=db["host"],
            port=int(db.get("port", 3306)),
            user=db["user"],
            password=db.get("password", ""),
            database=db["database"],
            charset=db.get("charset", "utf8mb4"),
        ),
        modules=ModulesConfig(
            creatures=bool(modules.get("creatures", True)),
            creature_stats=bool(modules.get("creature_stats", True)),
            loot=bool(modules.get("loot", True)),
            quests=bool(modules.get("quests", True)),
            item_stats=bool(modules.get("item_stats", True)),
        ),
        randomizer=RandomizerConfig(
            chunk_size=max(int(randomizer.get("chunk_size", 1000)), 100),
        ),
    )


T = TypeVar("T")


def chunks(items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class DB:
    def __init__(self, config: DatabaseConfig):
        self._config = config
        self.conn = pymysql.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            db=config.database,
            charset=config.charset,
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
        self.schema = config.database

    def close(self) -> None:
        self.conn.close()

    def execute(self, sql: str, params: tuple | list | None = None) -> int:
        with self.conn.cursor() as cur:
            count = cur.execute(sql, params)
        return count

    def executemany(self, sql: str, params: Sequence[tuple]) -> int:
        with self.conn.cursor() as cur:
            count = cur.executemany(sql, params)
        return count

    def fetchall(self, sql: str, params: tuple | list | None = None) -> List[dict]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    def fetchone(self, sql: str, params: tuple | list | None = None) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    def table_exists(self, table_name: str) -> bool:
        row = self.fetchone(
            "SELECT COUNT(*) AS c "
            "FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s",
            (self.schema, table_name),
        )
        return bool(row and row["c"] > 0)

    def get_columns(self, table_name: str) -> set[str]:
        rows = self.fetchall(
            "SELECT column_name "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s",
            (self.schema, table_name),
        )
        return {r["column_name"] for r in rows}


def ensure_meta_table(db: DB) -> None:
    db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS `{META_TABLE}` (
            `meta_key` VARCHAR(128) NOT NULL PRIMARY KEY,
            `meta_value` LONGTEXT NULL,
            `updated_at` TIMESTAMP NOT NULL
                DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def set_meta(db: DB, key: str, value: str) -> None:
    db.execute(
        f"""
        INSERT INTO `{META_TABLE}` (`meta_key`, `meta_value`)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE meta_value = VALUES(meta_value)
        """,
        (key, value),
    )


def get_meta(db: DB, key: str) -> str | None:
    row = db.fetchone(
        f"SELECT `meta_value` FROM `{META_TABLE}` WHERE `meta_key` = %s", (key,)
    )
    return None if not row else row["meta_value"]


def enabled_tables(config: AppConfig) -> List[str]:
    tables = set()
    if config.modules.creatures:
        tables.add(TABLE_CREATURE)
    if config.modules.creature_stats:
        tables.add(TABLE_CREATURE_TEMPLATE)
    if config.modules.loot:
        tables.add(TABLE_CREATURE_LOOT)
    if config.modules.quests:
        tables.add(TABLE_QUEST)
    if config.modules.item_stats:
        tables.add(TABLE_ITEM)
    return sorted(tables)


def backup_table_name(table_name: str) -> str:
    return f"{BACKUP_PREFIX}{table_name}"


def init_backup(db: DB, config: AppConfig) -> None:
    ensure_meta_table(db)
    target_tables = enabled_tables(config)
    if not target_tables:
        print("No modules enabled in config. Nothing to backup.")
        return

    for table in target_tables:
        if not db.table_exists(table):
            print(f"Skip backup: table `{table}` does not exist")
            continue

        backup = backup_table_name(table)
        db.execute(f"CREATE TABLE IF NOT EXISTS `{backup}` LIKE `{table}`")
        row = db.fetchone(f"SELECT COUNT(*) AS c FROM `{backup}`")
        count = int(row["c"]) if row else 0
        if count == 0:
            print(f"Backing up table `{table}` -> `{backup}`")
            db.execute(f"INSERT INTO `{backup}` SELECT * FROM `{table}`")
        else:
            print(f"Backup already exists for `{table}` ({count} rows)")

    set_meta(db, "backup_initialized", "1")
    set_meta(db, "backup_tables", json.dumps(target_tables))
    db.commit()
    print("Backup initialization completed.")


def restore_table(db: DB, table: str) -> None:
    backup = backup_table_name(table)
    if not db.table_exists(backup):
        raise RuntimeError(
            f"Backup table `{backup}` not found. Run `init-backup` first."
        )
    db.execute("SET FOREIGN_KEY_CHECKS = 0")
    db.execute(f"TRUNCATE TABLE `{table}`")
    db.execute(f"INSERT INTO `{table}` SELECT * FROM `{backup}`")
    db.execute("SET FOREIGN_KEY_CHECKS = 1")


def restore_world(db: DB, config: AppConfig) -> None:
    ensure_meta_table(db)
    target_tables = enabled_tables(config)
    for table in target_tables:
        if not db.table_exists(table):
            print(f"Skip restore: table `{table}` does not exist")
            continue
        print(f"Restoring `{table}` from backup")
        restore_table(db, table)
    db.commit()
    print("Restore completed.")


def shuffled_mapping(values: Sequence[int], rng: random.Random) -> Dict[int, int]:
    unique_values = sorted(set(int(v) for v in values if int(v) > 0))
    shuffled = unique_values[:]
    rng.shuffle(shuffled)
    return {
        old: new
        for old, new in zip(unique_values, shuffled)
        if int(old) > 0 and int(new) > 0 and old != new
    }


def apply_mapping_to_column(
    db: DB,
    table: str,
    column: str,
    mapping: Dict[int, int],
    chunk_size: int,
) -> int:
    if not mapping:
        return 0

    row = db.fetchone(f"SELECT COALESCE(MAX(`{column}`), 0) AS m FROM `{table}`")
    max_value = int(row["m"]) if row else 0
    offset = max(max_value + 100000, 1000000)

    changed = 0
    pairs = sorted(mapping.items())
    for chunk in chunks(pairs, chunk_size):
        when_parts = " ".join(f"WHEN {old} THEN {new + offset}" for old, new in chunk)
        in_list = ", ".join(str(old) for old, _ in chunk)
        sql = (
            f"UPDATE `{table}` "
            f"SET `{column}` = CASE `{column}` {when_parts} ELSE `{column}` END "
            f"WHERE `{column}` IN ({in_list})"
        )
        changed += db.execute(sql)

    changed += db.execute(
        f"UPDATE `{table}` SET `{column}` = `{column}` - %s WHERE `{column}` > %s",
        (offset, offset),
    )
    return changed


def randomize_creature_spawns(db: DB, config: AppConfig, rng: random.Random) -> int:
    if not db.table_exists(TABLE_CREATURE):
        print("Skip creatures randomization: table `creature` not found")
        return 0

    columns = db.get_columns(TABLE_CREATURE)
    required = {"guid", "id", "map"}
    if not required.issubset(columns):
        print("Skip creatures randomization: required columns missing")
        return 0

    rows = db.fetchall(
        f"SELECT `guid`, `id`, `map` FROM `{TABLE_CREATURE}` ORDER BY `guid` ASC"
    )
    if not rows:
        return 0

    grouped: Dict[int, List[dict]] = defaultdict(list)
    for row in rows:
        grouped[int(row["map"])].append(row)

    updates: List[Tuple[int, int]] = []
    for map_id in sorted(grouped):
        entries = grouped[map_id]
        ids = [int(r["id"]) for r in entries]
        shuffled_ids = ids[:]
        rng.shuffle(shuffled_ids)
        for row, new_id in zip(entries, shuffled_ids):
            guid = int(row["guid"])
            old_id = int(row["id"])
            if old_id != new_id:
                updates.append((new_id, guid))

    if not updates:
        return 0

    total = 0
    for chunk in chunks(updates, config.randomizer.chunk_size):
        total += db.executemany(
            f"UPDATE `{TABLE_CREATURE}` SET `id` = %s WHERE `guid` = %s",
            list(chunk),
        )
    return total


def randomize_creature_stats(db: DB, config: AppConfig, rng: random.Random) -> int:
    if not db.table_exists(TABLE_CREATURE_TEMPLATE):
        print(
            "Skip creature stats randomization: table `creature_template` not found"
        )
        return 0

    columns = db.get_columns(TABLE_CREATURE_TEMPLATE)
    if "entry" not in columns:
        print("Skip creature stats randomization: `entry` missing")
        return 0

    candidates = [
        "HealthModifier",
        "ManaModifier",
        "ArmorModifier",
        "DamageModifier",
        "ExperienceModifier",
        "BaseAttackTime",
        "RangeAttackTime",
        "speed_walk",
        "speed_run",
    ]
    stat_cols = [c for c in candidates if c in columns]
    if not stat_cols:
        print("Skip creature stats randomization: no known stat columns found")
        return 0

    sql = (
        f"SELECT `entry`, "
        + ", ".join(f"`{c}`" for c in stat_cols)
        + f" FROM `{TABLE_CREATURE_TEMPLATE}` ORDER BY `entry` ASC"
    )
    rows = db.fetchall(sql)
    if len(rows) < 2:
        return 0

    profiles = [tuple(row[c] for c in stat_cols) for row in rows]
    shuffled_profiles = profiles[:]
    rng.shuffle(shuffled_profiles)

    updates = []
    for row, profile in zip(rows, shuffled_profiles):
        current = tuple(row[c] for c in stat_cols)
        if current == profile:
            continue
        entry = int(row["entry"])
        updates.append(tuple(profile) + (entry,))

    if not updates:
        return 0

    set_clause = ", ".join(f"`{c}` = %s" for c in stat_cols)
    sql_update = (
        f"UPDATE `{TABLE_CREATURE_TEMPLATE}` "
        f"SET {set_clause} "
        "WHERE `entry` = %s"
    )

    total = 0
    for chunk in chunks(updates, config.randomizer.chunk_size):
        total += db.executemany(sql_update, list(chunk))
    return total


def randomize_loot_items(db: DB, config: AppConfig, rng: random.Random) -> int:
    if not db.table_exists(TABLE_CREATURE_LOOT):
        print("Skip loot randomization: table `creature_loot_template` not found")
        return 0

    columns = db.get_columns(TABLE_CREATURE_LOOT)
    if "Item" not in columns:
        print("Skip loot randomization: `Item` column missing")
        return 0

    rows = db.fetchall(
        f"SELECT DISTINCT `Item` AS item "
        f"FROM `{TABLE_CREATURE_LOOT}` "
        "WHERE `Item` > 0 "
        "ORDER BY `Item` ASC"
    )
    values = [int(r["item"]) for r in rows]
    mapping = shuffled_mapping(values, rng)
    return apply_mapping_to_column(
        db,
        TABLE_CREATURE_LOOT,
        "Item",
        mapping,
        config.randomizer.chunk_size,
    )


def randomize_item_stats(db: DB, config: AppConfig, rng: random.Random) -> int:
    if not db.table_exists(TABLE_ITEM):
        print("Skip item stats randomization: table `item_template` not found")
        return 0

    columns = db.get_columns(TABLE_ITEM)
    key_cols = ["entry", "class", "subclass", "InventoryType", "Quality"]
    if not all(c in columns for c in key_cols):
        print("Skip item stats randomization: key columns are missing")
        return 0

    stat_columns: List[str] = []
    for i in range(1, 11):
        t = f"stat_type{i}"
        v = f"stat_value{i}"
        if t in columns:
            stat_columns.append(t)
        if v in columns:
            stat_columns.append(v)
    optional_cols = [
        "dmg_min1",
        "dmg_max1",
        "dmg_min2",
        "dmg_max2",
        "armor",
        "delay",
        "block",
    ]
    stat_columns.extend(c for c in optional_cols if c in columns)
    stat_columns = list(dict.fromkeys(stat_columns))
    if not stat_columns:
        print("Skip item stats randomization: no known stat fields found")
        return 0

    sql = (
        "SELECT `entry`, `class`, `subclass`, `InventoryType`, `Quality`, "
        + ", ".join(f"`{c}`" for c in stat_columns)
        + f" FROM `{TABLE_ITEM}` ORDER BY `entry` ASC"
    )
    rows = db.fetchall(sql)
    if len(rows) < 2:
        return 0

    buckets: Dict[Tuple[int, int, int, int], List[dict]] = defaultdict(list)
    for row in rows:
        key = (
            int(row["class"]),
            int(row["subclass"]),
            int(row["InventoryType"]),
            int(row["Quality"]),
        )
        buckets[key].append(row)

    updates: List[tuple] = []
    for bucket_rows in buckets.values():
        if len(bucket_rows) < 2:
            continue
        profiles = [tuple(r[c] for c in stat_columns) for r in bucket_rows]
        shuffled_profiles = profiles[:]
        rng.shuffle(shuffled_profiles)

        for row, profile in zip(bucket_rows, shuffled_profiles):
            current = tuple(row[c] for c in stat_columns)
            if current == profile:
                continue
            updates.append(tuple(profile) + (int(row["entry"]),))

    if not updates:
        return 0

    set_clause = ", ".join(f"`{c}` = %s" for c in stat_columns)
    sql_update = f"UPDATE `{TABLE_ITEM}` SET {set_clause} WHERE `entry` = %s"
    total = 0
    for chunk in chunks(updates, config.randomizer.chunk_size):
        total += db.executemany(sql_update, list(chunk))
    return total


def randomize_quests(db: DB, config: AppConfig, rng: random.Random) -> int:
    if not db.table_exists(TABLE_QUEST):
        print("Skip quest randomization: table `quest_template` not found")
        return 0

    columns = db.get_columns(TABLE_QUEST)
    changed = 0

    creature_cols = [
        "ReqCreatureOrGOId1",
        "ReqCreatureOrGOId2",
        "ReqCreatureOrGOId3",
        "ReqCreatureOrGOId4",
    ]
    creature_cols = [c for c in creature_cols if c in columns]
    for column in creature_cols:
        rows = db.fetchall(
            f"SELECT DISTINCT `{column}` AS v FROM `{TABLE_QUEST}` WHERE `{column}` > 0"
        )
        values = [int(r["v"]) for r in rows]
        mapping = shuffled_mapping(values, rng)
        changed += apply_mapping_to_column(
            db, TABLE_QUEST, column, mapping, config.randomizer.chunk_size
        )

    item_cols = [
        "RewChoiceItemId1",
        "RewChoiceItemId2",
        "RewChoiceItemId3",
        "RewChoiceItemId4",
        "RewChoiceItemId5",
        "RewChoiceItemId6",
        "RewItemId1",
        "RewItemId2",
        "RewItemId3",
        "RewItemId4",
        "ReqItemId1",
        "ReqItemId2",
        "ReqItemId3",
        "ReqItemId4",
        "ReqItemId5",
        "ReqItemId6",
        "SrcItemId",
    ]
    item_cols = [c for c in item_cols if c in columns]
    for column in item_cols:
        rows = db.fetchall(
            f"SELECT DISTINCT `{column}` AS v FROM `{TABLE_QUEST}` WHERE `{column}` > 0"
        )
        values = [int(r["v"]) for r in rows]
        mapping = shuffled_mapping(values, rng)
        changed += apply_mapping_to_column(
            db, TABLE_QUEST, column, mapping, config.randomizer.chunk_size
        )

    return changed


def run_randomization(db: DB, config: AppConfig, seed: int) -> None:
    ensure_meta_table(db)
    rng = random.Random(seed)

    if config.modules.creatures and db.table_exists(TABLE_CREATURE):
        print("Restoring base table for creatures")
        restore_table(db, TABLE_CREATURE)
    if config.modules.creature_stats and db.table_exists(TABLE_CREATURE_TEMPLATE):
        print("Restoring base table for creature stats")
        restore_table(db, TABLE_CREATURE_TEMPLATE)
    if config.modules.loot and db.table_exists(TABLE_CREATURE_LOOT):
        print("Restoring base table for loot")
        restore_table(db, TABLE_CREATURE_LOOT)
    if config.modules.quests and db.table_exists(TABLE_QUEST):
        print("Restoring base table for quests")
        restore_table(db, TABLE_QUEST)
    if config.modules.item_stats and db.table_exists(TABLE_ITEM):
        print("Restoring base table for item stats")
        restore_table(db, TABLE_ITEM)

    summary: Dict[str, int] = {}

    if config.modules.creatures:
        summary["creatures"] = randomize_creature_spawns(db, config, rng)
    if config.modules.creature_stats:
        summary["creature_stats"] = randomize_creature_stats(db, config, rng)
    if config.modules.loot:
        summary["loot"] = randomize_loot_items(db, config, rng)
    if config.modules.quests:
        summary["quests"] = randomize_quests(db, config, rng)
    if config.modules.item_stats:
        summary["item_stats"] = randomize_item_stats(db, config, rng)

    set_meta(db, "last_seed", str(seed))
    set_meta(db, "last_run_epoch", str(int(time.time())))
    set_meta(db, "last_summary", json.dumps(summary, ensure_ascii=True))
    db.commit()

    print("Randomization completed.")
    print(f"Seed: {seed}")
    for key in sorted(summary):
        print(f"  {key}: {summary[key]} changes")


def show_status(db: DB) -> None:
    ensure_meta_table(db)
    row_seed = get_meta(db, "last_seed")
    row_epoch = get_meta(db, "last_run_epoch")
    row_summary = get_meta(db, "last_summary")
    print("Randomizer status")
    print(f"  last_seed: {row_seed or '-'}")
    if row_epoch:
        print(f"  last_run_epoch: {row_epoch}")
    else:
        print("  last_run_epoch: -")
    if row_summary:
        print(f"  last_summary: {row_summary}")
    else:
        print("  last_summary: -")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="WoW 3.3.5a world randomizer (mobs, loot, quests, stats)"
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to YAML config file",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-backup", help="Create one-time backup tables")
    sub.add_parser("restore", help="Restore all enabled modules from backup")
    sub.add_parser("status", help="Show last randomization status")

    randomize_parser = sub.add_parser("randomize", help="Randomize world using seed")
    randomize_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Deterministic seed. If omitted, current epoch is used.",
    )

    reroll_parser = sub.add_parser(
        "reroll", help="Alias for randomize with an auto-generated seed"
    )
    reroll_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional custom seed; if omitted, current epoch is used.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = parse_config(args.config)
    db = DB(config.database)
    try:
        if args.command == "init-backup":
            init_backup(db, config)
        elif args.command == "restore":
            restore_world(db, config)
        elif args.command in {"randomize", "reroll"}:
            seed = int(args.seed) if args.seed is not None else int(time.time())
            run_randomization(db, config, seed)
        elif args.command == "status":
            show_status(db)
        else:
            raise RuntimeError(f"Unsupported command: {args.command}")
    except Exception as exc:  # pylint: disable=broad-except
        db.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

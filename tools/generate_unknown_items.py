#!/usr/bin/env python3
"""Generate unidentified item copies for TrinityCore 3.3.5a.

This script creates unidentified copies of equippable items in world.item_template,
builds base<->unknown mapping, and optionally rewrites loot templates to drop
the unidentified entries.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
import sys
from typing import Any


LOOT_TABLE_CANDIDATES = [
    "creature_loot_template",
    "disenchant_loot_template",
    "fishing_loot_template",
    "gameobject_loot_template",
    "item_loot_template",
    "mail_loot_template",
    "milling_loot_template",
    "pickpocketing_loot_template",
    "prospecting_loot_template",
    "reference_loot_template",
    "skinning_loot_template",
    "spell_loot_template",
]


ZERO_FIELD_CANDIDATES = [
    "dmg_min1",
    "dmg_max1",
    "dmg_min2",
    "dmg_max2",
    "armor",
    "holy_res",
    "fire_res",
    "nature_res",
    "frost_res",
    "shadow_res",
    "arcane_res",
    "RandomProperty",
    "RandomSuffix",
]

for i in range(1, 11):
    ZERO_FIELD_CANDIDATES.append(f"stat_type{i}")
    ZERO_FIELD_CANDIDATES.append(f"stat_value{i}")


@dataclass
class DbConfig:
    host: str
    port: int
    user: str
    password: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create unidentified copies of equippable items and map loot drops "
            "to those copies."
        )
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="wow")
    parser.add_argument("--password", default="wow123")
    parser.add_argument("--world-db", default="world")
    parser.add_argument("--characters-db", default="characters")
    parser.add_argument(
        "--unknown-offset",
        type=int,
        default=1_000_000,
        help="Unknown entry = base entry + offset (default 1000000).",
    )
    parser.add_argument(
        "--unknown-name-prefix",
        default="[Unidentified] ",
        help="Prefix for copied unknown item names.",
    )
    parser.add_argument(
        "--unknown-description",
        default="Stats: Unknown",
        help="Description text for unknown item copies.",
    )
    parser.add_argument(
        "--force-quality",
        type=int,
        default=-1,
        help=(
            "Set copied unknown item quality to this value. "
            "Default -1 keeps original quality."
        ),
    )
    parser.add_argument(
        "--swap-loot",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rewrite loot template Item IDs to unknown entries.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show summary only; do not modify databases.",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=500,
        help="Batch size for bulk upserts (default 500).",
    )
    return parser.parse_args()


def connect(config: DbConfig, database: str):
    try:
        import mysql.connector  # type: ignore[import-untyped]
    except Exception as import_error:  # noqa: BLE001
        raise RuntimeError(
            "Missing dependency: mysql-connector-python "
            "(install with: python3 -m pip install mysql-connector-python)"
        ) from import_error

    return mysql.connector.connect(  # type: ignore[call-arg]
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=database,
        autocommit=False,
    )


def fetch_columns(conn, database: str, table: str) -> list[str]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        ORDER BY ORDINAL_POSITION
        """,
        (database, table),
    )
    columns = [row[0] for row in cursor.fetchall()]
    cursor.close()
    return columns


def pick_column(columns: list[str], names: list[str]) -> str | None:
    lower_map = {column.lower(): column for column in columns}
    for name in names:
        found = lower_map.get(name.lower())
        if found:
            return found
    return None


def table_exists(conn, database: str, table: str) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
        LIMIT 1
        """,
        (database, table),
    )
    exists = cursor.fetchone() is not None
    cursor.close()
    return exists


def table_has_column(conn, database: str, table: str, column: str) -> bool:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
        LIMIT 1
        """,
        (database, table, column),
    )
    exists = cursor.fetchone() is not None
    cursor.close()
    return exists


def ensure_world_schema(conn) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_identify_item_map (
            base_entry INT UNSIGNED NOT NULL,
            unknown_entry INT UNSIGNED NOT NULL,
            enabled TINYINT(1) NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (base_entry),
            UNIQUE KEY uq_unknown_entry (unknown_entry)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_identify_rarity_config (
            rarity TINYINT UNSIGNED NOT NULL,
            rarity_name VARCHAR(32) NOT NULL,
            weight INT UNSIGNED NOT NULL,
            mult_min DECIMAL(6,3) NOT NULL,
            mult_max DECIMAL(6,3) NOT NULL,
            dust_min SMALLINT UNSIGNED NOT NULL,
            dust_max SMALLINT UNSIGNED NOT NULL,
            PRIMARY KEY (rarity)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.executemany(
        """
        INSERT INTO custom_identify_rarity_config
            (rarity, rarity_name, weight, mult_min, mult_max, dust_min, dust_max)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            rarity_name = VALUES(rarity_name),
            weight = VALUES(weight),
            mult_min = VALUES(mult_min),
            mult_max = VALUES(mult_max),
            dust_min = VALUES(dust_min),
            dust_max = VALUES(dust_max)
        """,
        [
            (1, "common", 55, Decimal("0.800"), Decimal("1.100"), 1, 2),
            (2, "rare", 30, Decimal("1.000"), Decimal("1.250"), 3, 5),
            (3, "epic", 12, Decimal("1.150"), Decimal("1.450"), 8, 12),
            (4, "legendary", 3, Decimal("1.350"), Decimal("1.800"), 20, 30),
        ],
    )
    cursor.close()


def ensure_characters_schema(conn) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_identify_player_progress (
            guid INT UNSIGNED NOT NULL,
            dust INT UNSIGNED NOT NULL DEFAULT 0,
            pity_without_epic INT UNSIGNED NOT NULL DEFAULT 0,
            pity_without_legendary INT UNSIGNED NOT NULL DEFAULT 0,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (guid)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_identify_item_instance (
            item_guid INT UNSIGNED NOT NULL,
            owner_guid INT UNSIGNED NOT NULL,
            base_entry INT UNSIGNED NOT NULL,
            rarity TINYINT UNSIGNED NOT NULL DEFAULT 0,
            stat_multiplier DECIMAL(8,4) NOT NULL DEFAULT 1.0000,
            upgrade_rank TINYINT UNSIGNED NOT NULL DEFAULT 0,
            identified TINYINT(1) NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (item_guid),
            KEY idx_owner_guid (owner_guid),
            KEY idx_base_entry (base_entry)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.close()


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main() -> int:
    args = parse_args()
    if args.unknown_offset <= 0:
        print("--unknown-offset must be positive", file=sys.stderr)
        return 1
    if args.chunk_size < 1:
        print("--chunk-size must be >= 1", file=sys.stderr)
        return 1

    db_config = DbConfig(args.host, args.port, args.user, args.password)
    world_conn = connect(db_config, args.world_db)
    chars_conn = connect(db_config, args.characters_db)

    try:
        item_columns = fetch_columns(world_conn, args.world_db, "item_template")
        if not item_columns:
            raise RuntimeError("Table world.item_template was not found.")

        entry_col = pick_column(item_columns, ["entry"])
        class_col = pick_column(item_columns, ["class"])
        inv_col = pick_column(item_columns, ["InventoryType", "inventorytype"])
        name_col = pick_column(item_columns, ["name", "Name1"])
        quality_col = pick_column(item_columns, ["Quality", "quality"])
        desc_col = pick_column(item_columns, ["description", "Description"])

        if not all([entry_col, class_col, inv_col, name_col]):
            raise RuntimeError(
                "item_template missing required columns: entry/class/InventoryType/name"
            )

        world_cur = world_conn.cursor(dictionary=True)
        world_cur.execute(
            f"""
            SELECT *
            FROM item_template
            WHERE `{class_col}` IN (2, 4)
              AND `{inv_col}` > 0
              AND `{entry_col}` > 0
              AND `{entry_col}` < %s
            """,
            (args.unknown_offset,),
        )
        base_rows = world_cur.fetchall()
        world_cur.close()

        if not base_rows:
            print("No candidate equippable items found.")
            return 0

        map_rows: list[tuple[int, int]] = []
        unknown_rows: list[tuple[Any, ...]] = []

        lower_to_real = {column.lower(): column for column in item_columns}
        zero_fields = [
            lower_to_real[name.lower()]
            for name in ZERO_FIELD_CANDIDATES
            if name.lower() in lower_to_real
        ]

        for row in base_rows:
            base_entry = int(row[entry_col])
            unknown_entry = base_entry + args.unknown_offset
            if unknown_entry > 4_000_000_000:
                continue

            unknown_row = dict(row)
            unknown_row[entry_col] = unknown_entry
            unknown_row[name_col] = (
                f"{args.unknown_name_prefix}{row[name_col]}"[:255]
                if row[name_col]
                else args.unknown_name_prefix.strip()
            )
            if desc_col:
                unknown_row[desc_col] = args.unknown_description
            if quality_col and args.force_quality >= 0:
                unknown_row[quality_col] = args.force_quality

            for field in zero_fields:
                unknown_row[field] = 0

            map_rows.append((base_entry, unknown_entry))
            unknown_rows.append(tuple(unknown_row[column] for column in item_columns))

        print(f"Candidates: {len(base_rows)}")
        print(f"Unknown copies to write: {len(unknown_rows)}")

        if args.dry_run:
            for base_entry, unknown_entry in map_rows[:10]:
                print(f"  map {base_entry} -> {unknown_entry}")
            print("Dry run completed. No database writes.")
            return 0

        ensure_world_schema(world_conn)
        ensure_characters_schema(chars_conn)

        map_cur = world_conn.cursor()
        for part in chunked(map_rows, args.chunk_size):
            map_cur.executemany(
                """
                INSERT INTO custom_identify_item_map (base_entry, unknown_entry, enabled)
                VALUES (%s, %s, 1)
                ON DUPLICATE KEY UPDATE
                    unknown_entry = VALUES(unknown_entry),
                    enabled = VALUES(enabled)
                """,
                part,
            )
        map_cur.close()

        cols_sql = ", ".join(f"`{column}`" for column in item_columns)
        placeholders = ", ".join(["%s"] * len(item_columns))
        insert_sql = f"REPLACE INTO item_template ({cols_sql}) VALUES ({placeholders})"

        item_cur = world_conn.cursor()
        for part in chunked(unknown_rows, args.chunk_size):
            item_cur.executemany(insert_sql, part)
        item_cur.close()

        swapped_tables: list[str] = []
        if args.swap_loot:
            update_cur = world_conn.cursor()
            for table in LOOT_TABLE_CANDIDATES:
                if not table_exists(world_conn, args.world_db, table):
                    continue
                if not table_has_column(world_conn, args.world_db, table, "Item"):
                    continue
                update_cur.execute(
                    f"""
                    UPDATE `{table}` AS l
                    JOIN custom_identify_item_map AS m
                      ON l.`Item` = m.base_entry
                    SET l.`Item` = m.unknown_entry
                    """
                )
                swapped_tables.append(table)
            update_cur.close()

        world_conn.commit()
        chars_conn.commit()
        print("Done.")
        if swapped_tables:
            print(f"Loot tables rewritten: {', '.join(swapped_tables)}")
        else:
            print("Loot table rewrite skipped or no matching tables found.")
        return 0
    except Exception as error:  # noqa: BLE001
        world_conn.rollback()
        chars_conn.rollback()
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        world_conn.close()
        chars_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

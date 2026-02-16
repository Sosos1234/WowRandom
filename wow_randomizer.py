#!/usr/bin/env python3
"""WoW 3.3.5a world randomizer for private server databases.

This tool targets server-side world DBs (TrinityCore/AzerothCore-style schemas)
and generates a deterministic SQL script from a seed.
"""

from __future__ import annotations

import argparse
import os
import random
import secrets
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence, TypeVar


ITEM_STAT_FIELDS = [
    "RequiredLevel",
    "Quality",
    "delay",
    "armor",
    "holy_res",
    "fire_res",
    "nature_res",
    "frost_res",
    "shadow_res",
    "arcane_res",
    "dmg_min1",
    "dmg_max1",
    "dmg_type1",
    "dmg_min2",
    "dmg_max2",
    "dmg_type2",
]

for index in range(1, 11):
    ITEM_STAT_FIELDS.append(f"stat_type{index}")
    ITEM_STAT_FIELDS.append(f"stat_value{index}")


QUEST_FIELDS = []
for index in range(1, 7):
    QUEST_FIELDS.append(f"RewardChoiceItemId{index}")
    QUEST_FIELDS.append(f"RewardChoiceItemCount{index}")

for index in range(1, 5):
    QUEST_FIELDS.append(f"RewardItem{index}")
    QUEST_FIELDS.append(f"RewardAmount{index}")
    QUEST_FIELDS.append(f"ReqCreatureOrGOId{index}")
    QUEST_FIELDS.append(f"ReqCreatureOrGOCount{index}")
    QUEST_FIELDS.append(f"RequiredNpcOrGo{index}")
    QUEST_FIELDS.append(f"RequiredNpcOrGoCount{index}")

for index in range(1, 7):
    QUEST_FIELDS.append(f"ReqItemId{index}")
    QUEST_FIELDS.append(f"ReqItemCount{index}")

QUEST_FIELDS.extend(
    [
        "RewardOrRequiredMoney",
        "RewardMoneyMaxLevel",
        "RewardHonor",
        "RewardXPId",
    ]
)


def quote_ident(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


def quote_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def aliased(alias: str, column: str) -> str:
    return f"`{alias}`.{quote_ident(column)}"


T = TypeVar("T")


def chunks(values: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    mysql_binary: str = "mysql"


@dataclass
class RandomizationPlan:
    statements: list[str] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    touched_tables: set[str] = field(default_factory=set)

    def add_sql(self, sql: str) -> None:
        self.statements.append(sql)

    def add_sql_many(self, sql_list: Iterable[str]) -> None:
        self.statements.extend(sql_list)

    def add_summary(self, text: str) -> None:
        self.summaries.append(text)

    def mark_tables(self, tables: Iterable[str]) -> None:
        self.touched_tables.update(tables)

    @property
    def has_changes(self) -> bool:
        return bool(self.statements)


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

    def _base_command(self, binary: str | None = None) -> list[str]:
        executable = binary or self.config.mysql_binary
        return [
            executable,
            "-h",
            self.config.host,
            "-P",
            str(self.config.port),
            "-u",
            self.config.user,
            "-D",
            self.config.database,
        ]

    def run_query(self, query: str) -> list[list[str]]:
        command = self._base_command() + ["-N", "-B", "-e", query]
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            env=self._env(),
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"MySQL query failed (code {completed.returncode}): "
                f"{completed.stderr.strip() or 'unknown error'}"
            )
        rows: list[list[str]] = []
        for line in completed.stdout.splitlines():
            rows.append(line.split("\t"))
        return rows

    def run_sql_script(self, sql_text: str) -> None:
        command = self._base_command()
        completed = subprocess.run(
            command,
            text=True,
            input=sql_text,
            capture_output=True,
            env=self._env(),
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"MySQL script failed (code {completed.returncode}): "
                f"{completed.stderr.strip() or 'unknown error'}"
            )

    def dump_tables(
        self,
        tables: Sequence[str],
        output_path: Path,
        mysqldump_binary: str = "mysqldump",
    ) -> None:
        if not tables:
            return
        command = [
            mysqldump_binary,
            "-h",
            self.config.host,
            "-P",
            str(self.config.port),
            "-u",
            self.config.user,
            "--skip-lock-tables",
            self.config.database,
            *tables,
        ]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as dump_file:
            completed = subprocess.run(
                command,
                text=True,
                stdout=dump_file,
                stderr=subprocess.PIPE,
                env=self._env(),
                check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError(
                f"mysqldump failed (code {completed.returncode}): "
                f"{completed.stderr.strip() or 'unknown error'}"
            )


class WorldRandomizer:
    def __init__(self, client: MysqlClient, rng: random.Random, chunk_size: int = 500) -> None:
        self.client = client
        self.rng = rng
        self.chunk_size = chunk_size
        self._columns_cache: dict[str, list[str]] = {}

    def _pick_column(self, table_columns: Sequence[str], candidates: Sequence[str]) -> str | None:
        lower_map = {column.lower(): column for column in table_columns}
        for candidate in candidates:
            match = lower_map.get(candidate.lower())
            if match:
                return match
        return None

    def _existing_columns(
        self,
        table_columns: Sequence[str],
        candidates: Sequence[str],
    ) -> list[str]:
        lower_map = {column.lower(): column for column in table_columns}
        existing: list[str] = []
        for candidate in candidates:
            match = lower_map.get(candidate.lower())
            if match:
                existing.append(match)
        return existing

    def table_columns(self, table_name: str) -> list[str]:
        if table_name in self._columns_cache:
            return self._columns_cache[table_name]
        query = (
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA={quote_string(self.client.config.database)} "
            f"AND TABLE_NAME={quote_string(table_name)} "
            "ORDER BY ORDINAL_POSITION;"
        )
        rows = self.client.run_query(query)
        columns = [row[0] for row in rows if row]
        self._columns_cache[table_name] = columns
        return columns

    def _fetch_distinct_ids(
        self,
        table_name: str,
        column_name: str,
        where_clause: str | None = None,
    ) -> list[int]:
        query = f"SELECT DISTINCT {quote_ident(column_name)} FROM {quote_ident(table_name)}"
        if where_clause:
            query += f" WHERE {where_clause}"
        query += f" ORDER BY {quote_ident(column_name)};"
        rows = self.client.run_query(query)
        values: list[int] = []
        for row in rows:
            if not row:
                continue
            try:
                value = int(row[0])
            except (TypeError, ValueError):
                continue
            if value > 0:
                values.append(value)
        return values

    def _build_mapping(self, values: Sequence[int]) -> dict[int, int]:
        source = list(values)
        shuffled = source.copy()
        self.rng.shuffle(shuffled)
        if len(source) > 1 and shuffled == source:
            shuffled = shuffled[1:] + shuffled[:1]
        return dict(zip(source, shuffled, strict=True))

    def _case_update_statements(
        self,
        table_name: str,
        column_name: str,
        mapping: dict[int, int],
    ) -> list[str]:
        updates: list[str] = []
        sorted_pairs = sorted(mapping.items())
        for batch in chunks(sorted_pairs, self.chunk_size):
            case_blocks = " ".join(f"WHEN {src} THEN {dst}" for src, dst in batch)
            in_values = ", ".join(str(src) for src, _ in batch)
            statement = (
                f"UPDATE {quote_ident(table_name)} "
                f"SET {quote_ident(column_name)} = CASE {quote_ident(column_name)} "
                f"{case_blocks} ELSE {quote_ident(column_name)} END "
                f"WHERE {quote_ident(column_name)} IN ({in_values});"
            )
            updates.append(statement)
        return updates

    def _row_shuffle_statements(
        self,
        table_name: str,
        key_column: str,
        fields: Sequence[str],
        mapping: dict[int, int],
        temp_prefix: str,
    ) -> list[str]:
        if not fields:
            return []

        map_table = f"tmp_{temp_prefix}_map"
        snapshot_table = f"tmp_{temp_prefix}_snapshot"
        statements = [
            f"DROP TEMPORARY TABLE IF EXISTS {quote_ident(map_table)};",
            (
                f"CREATE TEMPORARY TABLE {quote_ident(map_table)} ("
                "`src` INT UNSIGNED PRIMARY KEY, "
                "`dst` INT UNSIGNED NOT NULL"
                ") ENGINE=MEMORY;"
            ),
        ]

        sorted_pairs = sorted(mapping.items())
        for batch in chunks(sorted_pairs, 1000):
            values = ", ".join(f"({src}, {dst})" for src, dst in batch)
            statements.append(
                f"INSERT INTO {quote_ident(map_table)} (`src`, `dst`) VALUES {values};"
            )

        snapshot_columns = ", ".join(
            [quote_ident(key_column), *(quote_ident(field) for field in fields)]
        )
        set_clause = ", ".join(
            f"{aliased('t', field)} = {aliased('s', field)}" for field in fields
        )

        statements.extend(
            [
                f"DROP TEMPORARY TABLE IF EXISTS {quote_ident(snapshot_table)};",
                (
                    f"CREATE TEMPORARY TABLE {quote_ident(snapshot_table)} AS "
                    f"SELECT {snapshot_columns} FROM {quote_ident(table_name)};"
                ),
                (
                    f"UPDATE {quote_ident(table_name)} AS `t` "
                    f"JOIN {quote_ident(map_table)} AS `m` "
                    f"ON {aliased('t', key_column)} = `m`.`src` "
                    f"JOIN {quote_ident(snapshot_table)} AS `s` "
                    f"ON {aliased('s', key_column)} = `m`.`dst` "
                    f"SET {set_clause};"
                ),
                f"DROP TEMPORARY TABLE IF EXISTS {quote_ident(map_table)};",
                f"DROP TEMPORARY TABLE IF EXISTS {quote_ident(snapshot_table)};",
            ]
        )
        return statements

    def randomize_mobs(self, plan: RandomizationPlan) -> None:
        table_name = "creature"
        columns = self.table_columns(table_name)
        if not columns:
            plan.add_summary("Mobs: table `creature` not found (skipped).")
            return

        id_column = self._pick_column(columns, ["id", "id1"])
        if not id_column:
            plan.add_summary("Mobs: no `id`/`id1` column in `creature` (skipped).")
            return

        values = self._fetch_distinct_ids(table_name, id_column)
        if len(values) < 2:
            plan.add_summary("Mobs: not enough rows to shuffle (skipped).")
            return

        mapping = self._build_mapping(values)
        plan.add_sql_many(self._case_update_statements(table_name, id_column, mapping))
        plan.mark_tables([table_name])
        plan.add_summary(f"Mobs: shuffled {len(mapping)} creature template IDs.")

    def randomize_loot(self, plan: RandomizationPlan) -> None:
        table_name = "creature_loot_template"
        columns = self.table_columns(table_name)
        if not columns:
            plan.add_summary("Loot: table `creature_loot_template` not found (skipped).")
            return

        entry_column = self._pick_column(columns, ["Entry", "entry"])
        if not entry_column:
            plan.add_summary("Loot: no `Entry` column in `creature_loot_template` (skipped).")
            return

        values = self._fetch_distinct_ids(table_name, entry_column)
        if len(values) < 2:
            plan.add_summary("Loot: not enough rows to shuffle (skipped).")
            return

        mapping = self._build_mapping(values)
        plan.add_sql_many(self._case_update_statements(table_name, entry_column, mapping))
        plan.mark_tables([table_name])
        plan.add_summary(f"Loot: shuffled {len(mapping)} creature loot entries.")

    def randomize_weapons(self, plan: RandomizationPlan) -> None:
        table_name = "item_template"
        columns = self.table_columns(table_name)
        if not columns:
            plan.add_summary("Weapons: table `item_template` not found (skipped).")
            return

        key_column = self._pick_column(columns, ["entry", "Entry", "ID", "id"])
        if not key_column:
            plan.add_summary("Weapons: no key column in `item_template` (skipped).")
            return

        class_column = self._pick_column(columns, ["class", "Class"])
        where_clause = None
        if class_column:
            where_clause = f"{quote_ident(class_column)} = 2"

        weapon_ids = self._fetch_distinct_ids(table_name, key_column, where_clause=where_clause)
        if len(weapon_ids) < 2:
            plan.add_summary("Weapons: not enough weapon rows to shuffle (skipped).")
            return

        fields = self._existing_columns(columns, ITEM_STAT_FIELDS)
        if not fields:
            plan.add_summary("Weapons: no known stat fields found (skipped).")
            return

        mapping = self._build_mapping(weapon_ids)
        statements = self._row_shuffle_statements(
            table_name=table_name,
            key_column=key_column,
            fields=fields,
            mapping=mapping,
            temp_prefix="weapon_stats",
        )
        plan.add_sql_many(statements)
        plan.mark_tables([table_name])
        plan.add_summary(
            f"Weapons: shuffled stats for {len(mapping)} rows across {len(fields)} fields."
        )

    def randomize_quests(self, plan: RandomizationPlan) -> None:
        table_name = "quest_template"
        columns = self.table_columns(table_name)
        if not columns:
            plan.add_summary("Quests: table `quest_template` not found (skipped).")
            return

        key_column = self._pick_column(columns, ["ID", "id", "entry", "Entry"])
        if not key_column:
            plan.add_summary("Quests: no key column in `quest_template` (skipped).")
            return

        fields = self._existing_columns(columns, QUEST_FIELDS)
        if not fields:
            plan.add_summary("Quests: no known quest fields found (skipped).")
            return

        quest_ids = self._fetch_distinct_ids(table_name, key_column)
        if len(quest_ids) < 2:
            plan.add_summary("Quests: not enough quests to shuffle (skipped).")
            return

        mapping = self._build_mapping(quest_ids)
        statements = self._row_shuffle_statements(
            table_name=table_name,
            key_column=key_column,
            fields=fields,
            mapping=mapping,
            temp_prefix="quest_rows",
        )
        plan.add_sql_many(statements)
        plan.mark_tables([table_name])
        plan.add_summary(
            f"Quests: shuffled data for {len(mapping)} quests across {len(fields)} fields."
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a seed-based SQL randomization script for WoW 3.3.5a world database "
            "(mobs, loot, weapon stats, quests)."
        )
    )
    parser.add_argument("--host", default=os.getenv("WOW_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("WOW_DB_PORT", "3306")))
    parser.add_argument("--user", default=os.getenv("WOW_DB_USER", "root"))
    parser.add_argument("--password", default=os.getenv("WOW_DB_PASSWORD", ""))
    parser.add_argument("--database", default=os.getenv("WOW_DB_NAME", "world"))
    parser.add_argument("--mysql-binary", default=os.getenv("WOW_MYSQL_BINARY", "mysql"))
    parser.add_argument(
        "--mysqldump-binary",
        default=os.getenv("WOW_MYSQLDUMP_BINARY", "mysqldump"),
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument(
        "--output-file",
        default=None,
        help="If set, overrides --output-dir/randomize_<seed>.sql",
    )
    parser.add_argument("--apply", action="store_true", help="Apply SQL to DB immediately.")
    parser.add_argument(
        "--backup-before-apply",
        action="store_true",
        help="Create mysqldump backup for touched tables before --apply.",
    )
    parser.add_argument(
        "--backup-dir",
        default="backup",
        help="Backup directory used when --backup-before-apply is enabled.",
    )
    parser.add_argument(
        "--mobs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable mob randomization.",
    )
    parser.add_argument(
        "--loot",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable loot randomization.",
    )
    parser.add_argument(
        "--weapons",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable weapon stat randomization.",
    )
    parser.add_argument(
        "--quests",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable quest randomization.",
    )
    return parser


def build_sql(seed: int, plan: RandomizationPlan) -> str:
    header = [
        "-- ------------------------------------------------------------",
        "-- WoW 3.3.5a world randomizer SQL",
        f"-- Generated: {datetime.now(timezone.utc).isoformat()}",
        f"-- Seed: {seed}",
        "-- ------------------------------------------------------------",
        "SET SESSION sql_safe_updates = 0;",
        "SET FOREIGN_KEY_CHECKS = 0;",
        "START TRANSACTION;",
    ]
    footer = [
        "COMMIT;",
        "SET FOREIGN_KEY_CHECKS = 1;",
    ]
    return "\n".join(header + plan.statements + footer) + "\n"


def resolve_seed(seed: int | None) -> int:
    if seed is not None:
        return seed
    return secrets.randbits(63)


def write_sql(sql_text: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(sql_text, encoding="utf-8")


def make_output_path(seed: int, output_dir: str, output_file: str | None) -> Path:
    if output_file:
        return Path(output_file)
    return Path(output_dir) / f"randomize_{seed}.sql"


def run(argv: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.chunk_size < 1:
        parser.error("--chunk-size must be >= 1")

    if not any((args.mobs, args.loot, args.weapons, args.quests)):
        parser.error("At least one category must be enabled.")

    seed = resolve_seed(args.seed)
    rng = random.Random(seed)
    db_config = DbConfig(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        mysql_binary=args.mysql_binary,
    )

    client = MysqlClient(db_config)
    randomizer = WorldRandomizer(client=client, rng=rng, chunk_size=args.chunk_size)
    plan = RandomizationPlan()

    if args.mobs:
        randomizer.randomize_mobs(plan)
    if args.loot:
        randomizer.randomize_loot(plan)
    if args.weapons:
        randomizer.randomize_weapons(plan)
    if args.quests:
        randomizer.randomize_quests(plan)

    if not plan.has_changes:
        print("No SQL changes were generated.")
        for summary in plan.summaries:
            print(f"- {summary}")
        return 1

    output_path = make_output_path(seed, args.output_dir, args.output_file)
    sql_text = build_sql(seed, plan)
    write_sql(sql_text, output_path)

    print(f"Seed: {seed}")
    print(f"SQL written to: {output_path}")
    print("Summary:")
    for summary in plan.summaries:
        print(f"- {summary}")

    if args.apply:
        if args.backup_before_apply:
            backup_name = f"world_backup_{seed}.sql"
            backup_path = Path(args.backup_dir) / backup_name
            tables = sorted(plan.touched_tables)
            client.dump_tables(tables, backup_path, mysqldump_binary=args.mysqldump_binary)
            print(f"Backup created: {backup_path}")
        client.run_sql_script(sql_text)
        print("SQL applied to database.")
    else:
        print("Dry run mode: SQL generated only. Use --apply to execute.")

    return 0


def main() -> None:
    try:
        exit_code = run(sys.argv[1:])
    except KeyboardInterrupt:
        print("Interrupted by user.")
        exit_code = 130
    except Exception as error:  # noqa: BLE001
        print(f"Error: {error}", file=sys.stderr)
        exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()

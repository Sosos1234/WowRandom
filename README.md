# WoW 3.3.5a World Randomizer

This repository contains a script that randomizes server-side world data for
WoW 3.3.5a private server databases (TrinityCore/AzerothCore-style schemas).

It can shuffle:
- mob template IDs used by spawns (`creature`)
- creature loot entries (`creature_loot_template`)
- weapon stats (`item_template`, `class = 2`)
- quest rewards/objectives (`quest_template`)
- random stat rolls for item stats (`item_template`, `stat_value1..10`)

The script is seed-based, so each seed corresponds to one generated world.
Use a new seed at any time to "reroll" the world.

## Important

This does **not** patch the standalone WoW client itself.
To get real gameplay randomization, you must run a private server and modify
its world database.

## Requirements

- Python 3.10+
- `mysql` CLI installed and reachable in `PATH`
- access credentials to your world database

## Basic usage

Generate SQL only (safe dry run):

```bash
python3 wow_randomizer.py \
  --host 127.0.0.1 \
  --port 3306 \
  --user root \
  --password "your_password" \
  --database world
```

Apply immediately to DB:

```bash
python3 wow_randomizer.py \
  --host 127.0.0.1 \
  --port 3306 \
  --user root \
  --password "your_password" \
  --database world \
  --apply
```

Use an explicit seed:

```bash
python3 wow_randomizer.py --seed 123456789 --apply
```

Enable backup before apply:

```bash
python3 wow_randomizer.py --apply --backup-before-apply
```

Random stat rolls for item stats (example: from -50% to +100% of original):

```bash
python3 wow_randomizer.py \
  --apply \
  --no-mobs --no-loot --no-weapons --no-quests \
  --item-stat-rolls \
  --item-stat-roll-min 0.5 \
  --item-stat-roll-max 2.0
```

## Category toggles

By default all categories are enabled. You can disable any:

```bash
python3 wow_randomizer.py --no-quests --no-weapons
```

Available toggles:
- `--mobs` / `--no-mobs`
- `--loot` / `--no-loot`
- `--weapons` / `--no-weapons`
- `--quests` / `--no-quests`
- `--item-stat-rolls` / `--no-item-stat-rolls`

## Environment variables (optional)

You can avoid repeating connection flags:

- `WOW_DB_HOST`
- `WOW_DB_PORT`
- `WOW_DB_USER`
- `WOW_DB_PASSWORD`
- `WOW_DB_NAME`
- `WOW_MYSQL_BINARY`
- `WOW_MYSQLDUMP_BINARY`

Then run simply:

```bash
python3 wow_randomizer.py --apply
```

## Output

- SQL files are saved to `output/randomize_<seed>.sql` by default.
- Backups are saved to `backup/world_backup_<seed>.sql` when enabled.

## Safety checklist

1. Stop world/auth daemons or ensure no active players.
2. Create a full DB backup.
3. Run randomizer.
4. Restart world server.
5. Test core gameplay flows (combat, questing, loot, vendors).

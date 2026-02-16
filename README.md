# WoW 3.3.5a World Randomizer

Script-based randomizer for 3.3.5a private servers (AzerothCore/TrinityCore-like world DB).

It can randomize:

- creature spawns (`creature.id` shuffled per map),
- creature combat modifiers (`creature_template` key stat fields),
- creature loot item IDs (`creature_loot_template.Item`),
- quest creature/item references in common quest fields (`quest_template`),
- item stat profiles (`item_template`, shuffled inside class/subclass/inventory/quality buckets).

The script supports **reroll** with a new seed and a full **restore** to the original baseline.

---

## Important notes

1. Use this only on a backup/staging realm first.
2. Run this while world server is stopped, or during maintenance window.
3. `init-backup` creates persistent backup tables (`wr_backup_*`) once.
4. Every `randomize`/`reroll` restores from backup first, then applies a new randomization.

---

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Edit `config.yaml` with your world DB credentials.

---

## Commands

Initialize baseline backup:

```bash
python3 wow_randomizer.py --config config.yaml init-backup
```

Randomize with explicit seed:

```bash
python3 wow_randomizer.py --config config.yaml randomize --seed 123456
```

Reroll with new auto-seed:

```bash
python3 wow_randomizer.py --config config.yaml reroll
```

Restore original world data from backup:

```bash
python3 wow_randomizer.py --config config.yaml restore
```

Show last run metadata:

```bash
python3 wow_randomizer.py --config config.yaml status
```

---

## What this modifies in DB

- `creature`
- `creature_template`
- `creature_loot_template`
- `quest_template`
- `item_template`

It also creates:

- `wr_backup_<table>` backup tables
- `wr_randomizer_meta` metadata table

---

## Quick workflow for "new world each run"

1. Stop realm/world server.
2. Run `reroll`.
3. Start realm/world server.
4. If needed, run `restore` to get vanilla baseline again.

## Мифик+ ключи (MVP) для TrinityCore 3.3.5a

Этот репозиторий содержит **минимально рабочую** реализацию Мифик+ ключей без клиент‑патча:

- **НПЦ “Keystone Master”** (template entry `900001`) выдаёт/меняет ключ игроку
- **Мировые Врата** (template entry `900002`) спавнятся раз в час в случайной точке
- **Ключ хранится в БД characters** (`custom_mplus_player_key`)
- **История забегов** хранится в БД characters (`custom_mplus_run_history`)
- **Список подземелий** хранится в БД world (`custom_mplus_dungeon`)
- **Точки спавна Врат** хранятся в БД world (`custom_mplus_world_portal_spawn`)
- **Скейл** делается без правок ядра через `UnitScript` (меняем урон):
  - урон игрок→моб уменьшается (эффективно увеличивает HP мобов)
  - урон моб→игрок увеличивается

### Что нужно на сервере

- TrinityCore 3.3.5a (или близкий по API)
- доступ к `world` и `characters` БД
- пересборка core (добавляется один C++ скрипт)

### Установка SQL (world/characters)

Самый простой путь — через установщик:

```bash
python3 tools/install_mythic_plus.py \
  --host 127.0.0.1 --port 3306 --user wow --password "wow123" \
  --world-db world --characters-db characters \
  --apply
```

Он применит:
- `sql/mythic_plus_world_schema.sql`
- `sql/mythic_plus_characters_schema.sql`
- `sql/mythic_plus_seed_wotlk_dungeons.sql` (можно отключить `--no-seed-wotlk-dungeons`)
- `sql/mythic_plus_world_portal_spawn_seed.sql` (можно отключить `--no-seed-world-portals`)
- `sql/mythic_plus_world_content.sql` (предмет/НПЦ, можно отключить `--no-install-content`)

### Подключение C++ скрипта в TrinityCore

1) Скопируй файл:

- `trinitycore/custom_scripts/mythic_plus_mvp.cpp`

в твой TrinityCore, например в:

- `src/server/scripts/Custom/mythic_plus_mvp.cpp`

2) Подключи загрузку скрипта в `src/server/scripts/Custom/custom_script_loader.cpp`:

- добавь объявление:

```cpp
void AddSC_custom_mythic_plus_mvp();
```

- и вызов внутри `AddCustomScripts()`:

```cpp
AddSC_custom_mythic_plus_mvp();
```

3) Пересобери core как обычно (cmake + make / ninja).

### Спавн НПЦ

В SQL **не** добавлены координаты спавна (они разные на каждом сервере). Спавни в игре:

- `.npc add 900001`

### Как это работает для игроков

1) Игрок говорит с **Keystone Master** и выбирает:
- “Выдать предмет-ключ (камень)” — выдаст предмет `900000` (токен)
- “Сгенерировать новый ключ (рандом)” — запишет ключ в БД и (если нужно) выдаст токен
- “Выбрать подземелье для ключа” — установит конкретный данж
- “Создать мировые врата здесь (тест)” — мгновенно создаёт активный тест-портал в текущей открытой локации

2) Лидер группы заходит в **соответствующее** 5p подземелье **с токеном** `900000` в сумке.

3) При входе лидера в инстанс Мифик+ автоматически стартует для `instanceId`.

4) Убийство **финального босса** (по `final_boss_entry` в `custom_mplus_dungeon`) завершает забег:
- пишется запись в `custom_mplus_run_history`
- выдаётся награда (по умолчанию `47241` * N)
- ключ лидера повышается на +1 (кап 25) и рандомит подземелье

### Мировые Врата (F..S)

- Раз в час появляется случайный портал (`Unstable Mythic Gate`, entry `900002`)
- Портал получает визуальный эффект (spell 32264) и случайный ранг опасности: `F, E, D, C, B, A, S`
- Вокруг портала появляется визуальное кольцо-маркер радиуса (entry `900003`)
- Каждые 60 сек рядом с порталом спавнятся волны мобов (2–5 шт, радиус 25 м)
- За каждый ранг даётся бонус к дропу Mythic Keystone:
  - `F = +10%`
  - `E = +20%`
  - `D = +30%`
  - `C = +40%`
  - `B = +50%`
  - `A = +60%`
  - `S = +70%`
- Бонус работает **только рядом с активным порталом** (радиус в коде: `120 м`)
- Если игрок далеко от портала или на другой карте — действует только базовый шанс

### Аффиксы (MVP)

Сейчас это просто **битовая маска** `custom_mplus_weekly.affix_mask`:

- `1` = Fortified (бафф на трэш)
- `2` = Tyrannical (бафф на боссов)

Скейл в коде учитывает `Creature::IsDungeonBoss()`:
- Fortified применяется к не‑боссам
- Tyrannical применяется к боссам

### Настройка под себя

Главные места, которые обычно правят:

- SQL предмета/НПЦ: `sql/mythic_plus_world_content.sql`
- Список данжей: `sql/mythic_plus_seed_wotlk_dungeons.sql`
- Точки спавна врат: `sql/mythic_plus_world_portal_spawn_seed.sql`
- Награда/формулы скейла: `trinitycore/custom_scripts/mythic_plus_mvp.cpp`

**Портал (эффект и волны):**

- `MPLUS_PORTAL_VISUAL_SPELL` (32264) — spell для визуального эффекта на портале. Если эффекта нет, проверь наличие spell в DBC или смени на 35717/61722.
- `MPLUS_WAVE_CREATURE_ENTRIES` — creature entries для волн (2560, 2561, 113 по умолчанию). Мобы должны существовать в `creature_template`.
- `MPLUS_WAVE_INTERVAL_MS` (60000) — интервал между волнами в мс.

### Ограничения MVP

- Таймер/скорость сейчас фиксируются только через `start_unix/end_unix` (без UI).
- Скейл “HP” сделан через уменьшение урона по мобам (это влияет и на threat).
- Не все смерти учитываются (счётчик увеличивается в `OnPlayerKilledByCreature`).
- Если `final_boss_entry` в твоей базе отличается, завершение не сработает — поправь таблицу `custom_mplus_dungeon`.


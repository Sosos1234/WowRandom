-- Mythic+ (MVP) world content
-- Creates:
-- - Keystone token item (entry 900000)
-- - Keystone Master NPC template (entry 900001) with ScriptName 'npc_mplus_keystone_master'
--
-- Spawning the NPC is intentionally NOT done here (coords differ per server).
-- You can spawn in-game as GM:
--   .npc add 900001

DELETE FROM item_template WHERE entry = 900000;
INSERT INTO item_template (
    entry, class, subclass, name, displayid, Quality,
    Flags, FlagsExtra,
    BuyCount, BuyPrice, SellPrice,
    InventoryType, AllowableClass, AllowableRace,
    ItemLevel, RequiredLevel,
    maxcount, stackable,
    bonding, description
)
VALUES (
    900000, 15, 0,
    'Mythic Keystone',
    6557,
    2,
    0, 0,
    1, 0, 0,
    0, -1, -1,
    1, 1,
    1, 1,
    1,
    'Token item used to activate Mythic+ runs (custom).'
);

DELETE FROM creature_template WHERE entry = 900001;
INSERT INTO creature_template (
    entry, modelid1, name, subname,
    minlevel, maxlevel, exp,
    faction, npcflag,
    unit_class, type,
    ScriptName
)
VALUES (
    900001, 1976,
    'Keystone Master',
    'Mythic+',
    80, 80, 2,
    35, 1,
    1, 7,
    'npc_mplus_keystone_master'
);

-- World Gate creature (hourly random open-world portal event)
DELETE FROM creature_template WHERE entry = 900002;
INSERT INTO creature_template (
    entry, modelid1, name, subname,
    minlevel, maxlevel, exp,
    faction, npcflag,
    unit_class, type,
    ScriptName
)
VALUES (
    900002, 18877,
    'Unstable Mythic Gate',
    'Hourly World Event',
    83, 83, 2,
    35, 0,
    1, 7,
    ''
);


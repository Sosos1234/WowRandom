-- Seed a default WotLK 5p dungeon list for Mythic+ (3.3.5a).
-- Notes:
-- - map_id is the instance map ID
-- - final_boss_entry is the creature_template.entry of the final boss (used to mark completion)
-- - If your world DB uses different entries, adjust accordingly.

INSERT INTO custom_mplus_dungeon (name, map_id, final_boss_entry, enabled)
VALUES
    ('Utgarde Keep', 574, 23954, 1),
    ('Utgarde Pinnacle', 575, 26861, 1),
    ('The Nexus', 576, 26723, 1),
    ('The Oculus', 578, 27656, 1),
    ('The Culling of Stratholme', 595, 26533, 1),
    ('Drak''Tharon Keep', 600, 26632, 1),
    ('Azjol-Nerub', 601, 29120, 1),
    ('Halls of Lightning', 602, 28923, 1),
    ('Gundrak', 604, 29306, 1),
    ('The Violet Hold', 608, 31134, 1),
    ('Ahn''kahet: The Old Kingdom', 619, 29311, 1),
    ('The Forge of Souls', 632, 36502, 1),
    ('Pit of Saron', 658, 36658, 1),
    ('Trial of the Champion', 650, 35451, 1)
ON DUPLICATE KEY UPDATE
    final_boss_entry = VALUES(final_boss_entry),
    enabled = VALUES(enabled),
    updated_at = CURRENT_TIMESTAMP;


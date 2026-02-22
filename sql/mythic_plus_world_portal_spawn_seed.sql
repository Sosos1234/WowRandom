-- Seed open-world Mythic portal spawn points (hourly random gate)
-- You can edit/add your own points anytime.

INSERT INTO custom_mplus_world_portal_spawn
    (spawn_id, name, map_id, position_x, position_y, position_z, orientation, enabled)
VALUES
    (1, 'Borean Tundra - Coldarra coast', 571, 5830.0, 640.0, 647.0, 0.0, 1),
    (2, 'Howling Fjord - Utgarde outskirts', 571, 934.0, -4908.0, 15.0, 0.0, 1),
    (3, 'Dragonblight - Wyrmrest approach', 571, 3548.0, 267.0, 46.0, 0.0, 1),
    (4, 'Grizzly Hills - Amberpine road', 571, 3260.0, -2280.0, 115.0, 0.0, 1),
    (5, 'Zul''Drak - Amphitheater route', 571, 5537.0, -3216.0, 372.0, 0.0, 1),
    (6, 'Sholazar Basin - River''s Heart', 571, 5592.0, 5816.0, -69.0, 0.0, 1),
    (7, 'Storm Peaks - K3 path', 571, 6667.0, -1025.0, 408.0, 0.0, 1),
    (8, 'Icecrown - Tournament pass', 571, 8477.0, 947.0, 547.0, 0.0, 1)
ON DUPLICATE KEY UPDATE
    name = VALUES(name),
    map_id = VALUES(map_id),
    position_x = VALUES(position_x),
    position_y = VALUES(position_y),
    position_z = VALUES(position_z),
    orientation = VALUES(orientation),
    enabled = VALUES(enabled),
    updated_at = CURRENT_TIMESTAMP;

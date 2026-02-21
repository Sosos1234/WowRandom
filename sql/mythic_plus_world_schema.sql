-- Mythic+ (MVP) schema - WORLD database
-- Target: TrinityCore 3.3.5a style schemas

CREATE TABLE IF NOT EXISTS custom_mplus_dungeon (
    dungeon_id SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    name VARCHAR(96) NOT NULL,
    map_id SMALLINT UNSIGNED NOT NULL,
    final_boss_entry INT UNSIGNED NOT NULL DEFAULT 0,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (dungeon_id),
    UNIQUE KEY uq_map_id (map_id),
    KEY idx_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Weekly rotation (affixes are represented as a bitmask)
CREATE TABLE IF NOT EXISTS custom_mplus_weekly (
    id TINYINT UNSIGNED NOT NULL DEFAULT 1,
    week_start_unix INT UNSIGNED NOT NULL DEFAULT 0,
    affix_mask INT UNSIGNED NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO custom_mplus_weekly (id, week_start_unix, affix_mask)
VALUES (1, 0, 0)
ON DUPLICATE KEY UPDATE
    updated_at = CURRENT_TIMESTAMP;


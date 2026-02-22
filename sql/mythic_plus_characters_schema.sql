-- Mythic+ (MVP) schema - CHARACTERS database
-- Target: TrinityCore 3.3.5a style schemas

CREATE TABLE IF NOT EXISTS custom_mplus_player_key (
    guid INT UNSIGNED NOT NULL,
    dungeon_id SMALLINT UNSIGNED NOT NULL,
    level TINYINT UNSIGNED NOT NULL DEFAULT 2,
    affix_mask INT UNSIGNED NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (guid),
    KEY idx_dungeon_level (dungeon_id, level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS custom_mplus_run_history (
    run_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    leader_guid INT UNSIGNED NOT NULL,
    dungeon_id SMALLINT UNSIGNED NOT NULL,
    map_id SMALLINT UNSIGNED NOT NULL,
    instance_id INT UNSIGNED NOT NULL,
    level TINYINT UNSIGNED NOT NULL,
    affix_mask INT UNSIGNED NOT NULL DEFAULT 0,
    start_unix INT UNSIGNED NOT NULL,
    end_unix INT UNSIGNED NOT NULL DEFAULT 0,
    duration_ms INT UNSIGNED NOT NULL DEFAULT 0,
    deaths SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    success TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id),
    KEY idx_leader (leader_guid, start_unix),
    KEY idx_dungeon (dungeon_id, level, start_unix),
    KEY idx_instance (instance_id, start_unix)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


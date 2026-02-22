-- World DB schema for unidentified item system

CREATE TABLE IF NOT EXISTS custom_identify_item_map (
    base_entry INT UNSIGNED NOT NULL,
    unknown_entry INT UNSIGNED NOT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (base_entry),
    UNIQUE KEY uq_unknown_entry (unknown_entry)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS custom_identify_rarity_config (
    rarity TINYINT UNSIGNED NOT NULL,
    rarity_name VARCHAR(32) NOT NULL,
    weight INT UNSIGNED NOT NULL,
    mult_min DECIMAL(6,3) NOT NULL,
    mult_max DECIMAL(6,3) NOT NULL,
    dust_min SMALLINT UNSIGNED NOT NULL,
    dust_max SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (rarity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO custom_identify_rarity_config
    (rarity, rarity_name, weight, mult_min, mult_max, dust_min, dust_max)
VALUES
    (1, 'common', 55, 0.800, 1.100, 1, 2),
    (2, 'rare', 30, 1.000, 1.250, 3, 5),
    (3, 'epic', 12, 1.150, 1.450, 8, 12),
    (4, 'legendary', 3, 1.350, 1.800, 20, 30)
ON DUPLICATE KEY UPDATE
    rarity_name = VALUES(rarity_name),
    weight = VALUES(weight),
    mult_min = VALUES(mult_min),
    mult_max = VALUES(mult_max),
    dust_min = VALUES(dust_min),
    dust_max = VALUES(dust_max);

-- Characters DB schema for player/item progression

CREATE TABLE IF NOT EXISTS custom_identify_player_progress (
    guid INT UNSIGNED NOT NULL,
    dust INT UNSIGNED NOT NULL DEFAULT 0,
    pity_without_epic INT UNSIGNED NOT NULL DEFAULT 0,
    pity_without_legendary INT UNSIGNED NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (guid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

/*
 * Mythic+ (MVP) for TrinityCore 3.3.5a
 *
 * What this provides (server-side only, no client patch):
 * - Keystone Master NPC (template entry 900001) that assigns a key to the player
 * - Character key stored in characters DB: custom_mplus_player_key
 * - Run tracking stored in characters DB: custom_mplus_run_history
 * - Dungeon list stored in world DB: custom_mplus_dungeon
 * - Scaling via UnitScript by modifying damage:
 *   - player -> creature damage is reduced (effective HP increase)
 *   - creature -> player damage is increased
 *
 * Activation model (MVP):
 * - Group leader obtains a key from the Keystone Master and keeps a token item (900000).
 * - When the leader enters the matching 5p dungeon instance while carrying the token,
 *   the Mythic+ run starts automatically for that instanceId.
 * - Killing the configured final boss completes the run and upgrades the key.
 *
 * Integration:
 * - Place this file into your TrinityCore sources (e.g. src/server/scripts/Custom/)
 * - Register AddSC_custom_mythic_plus_mvp() in custom_script_loader.cpp
 * - Apply SQL from this repo: sql/mythic_plus_*.sql
 */

#include "ScriptMgr.h"
#include "ScriptedCreature.h"
#include "ScriptedGossip.h"

#include "Chat.h"
#include "Creature.h"
#include "DatabaseEnv.h"
#include "Group.h"
#include "Log.h"
#include "Map.h"
#include "MapManager.h"
#include "ObjectAccessor.h"
#include "Player.h"
#include "Random.h"
#include "WorldSession.h"

#include <algorithm>
#include <cstdint>
#include <ctime>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace
{
    constexpr uint32 MPLUS_KEYSTONE_ITEM_ENTRY = 900000;
    constexpr uint32 MPLUS_WORLD_GATE_ENTRY = 900002;

    // Reward (change freely; must exist client-side)
    constexpr uint32 MPLUS_REWARD_ITEM_ENTRY = 47241; // Emblem of Triumph

    constexpr uint32 MPLUS_PORTAL_SPAWN_INTERVAL_SECONDS = 3600; // once per hour
    constexpr uint32 MPLUS_PORTAL_ACTIVE_SECONDS = 3600;         // active for one hour
    constexpr float MPLUS_BASE_KEY_DROP_CHANCE = 1.0f;           // base open-world key chance
    constexpr float MPLUS_PORTAL_BONUS_RADIUS = 120.0f;          // bonus works only near gate

    enum MPlusAffixMask : uint32
    {
        MPLUS_AFFIX_FORTIFIED  = 1u << 0,
        MPLUS_AFFIX_TYRANNICAL = 1u << 1,
    };

    struct DungeonDef
    {
        uint16 dungeonId = 0;
        uint16 mapId = 0;
        uint32 finalBossEntry = 0;
        std::string name;
        bool enabled = false;
    };

    struct ActiveRun
    {
        uint32 instanceId = 0;
        uint16 mapId = 0;
        uint16 dungeonId = 0;
        uint32 finalBossEntry = 0;
        std::string dungeonName;

        ObjectGuid::LowType leaderGuid = 0;
        uint8 level = 2;
        uint32 affixMask = 0;

        std::time_t startUnix = 0;
        uint16 deaths = 0;
        bool completed = false;
    };

    struct PortalSpawnDef
    {
        uint32 spawnId = 0;
        uint16 mapId = 0;
        Position pos;
        std::string name;
    };

    struct ActivePortal
    {
        bool active = false;
        uint32 spawnId = 0;
        uint16 mapId = 0;
        Position pos;
        uint8 rankIndex = 0; // 0..6 = F..S
        uint32 bonusPct = 0;
        std::time_t expiresUnix = 0;
        ObjectGuid portalGuid = ObjectGuid::Empty;
    };

    char const* PortalRankName(uint8 rankIndex)
    {
        static char const* kNames[] = {"F", "E", "D", "C", "B", "A", "S"};
        if (rankIndex >= 7)
            return "F";
        return kNames[rankIndex];
    }

    class MythicPlusMgr
    {
    public:
        static MythicPlusMgr& Instance()
        {
            static MythicPlusMgr instance;
            return instance;
        }

        void EnsureLoaded()
        {
            if (_loaded)
                return;
            LoadFromDb();
        }

        void Update(uint32 diff)
        {
            _cleanupTimer += diff;
            if (_cleanupTimer < 10'000)
                return;
            _cleanupTimer = 0;

            HandleWorldPortalTick();

            if (_activeRuns.empty())
                return;

            // Clean up runs whose instance maps no longer exist.
            std::vector<uint32> toRemove;
            toRemove.reserve(_activeRuns.size());

            for (auto const& [instanceId, run] : _activeRuns)
            {
                if (MapManager::instance()->FindMap(run.mapId, instanceId))
                    continue;
                toRemove.push_back(instanceId);
            }

            for (uint32 instanceId : toRemove)
            {
                auto itr = _activeRuns.find(instanceId);
                if (itr == _activeRuns.end())
                    continue;

                // Mark as failed if not completed.
                if (!itr->second.completed)
                    PersistRunHistory(itr->second, /*success=*/false, /*endUnix=*/std::time(nullptr));

                _activeRuns.erase(itr);
            }
        }

        std::vector<DungeonDef> const& GetDungeons() const { return _dungeons; }

        DungeonDef const* GetDungeonById(uint16 dungeonId) const
        {
            for (DungeonDef const& d : _dungeons)
                if (d.dungeonId == dungeonId)
                    return &d;
            return nullptr;
        }

        DungeonDef const* GetRandomEnabledDungeon() const
        {
            std::vector<DungeonDef const*> enabled;
            enabled.reserve(_dungeons.size());
            for (DungeonDef const& d : _dungeons)
                if (d.enabled)
                    enabled.push_back(&d);

            if (enabled.empty())
                return nullptr;

            return enabled[urand(0u, uint32(enabled.size() - 1))];
        }

        uint32 GetWeeklyAffixMask() const { return _weeklyAffixMask; }
        bool HasActivePortal() const { return _activePortal.active; }
        uint32 GetActivePortalBonusPct() const { return _activePortal.active ? _activePortal.bonusPct : 0; }
        uint32 GetActivePortalMapId() const { return _activePortal.active ? _activePortal.mapId : 0; }
        char const* GetActivePortalRankName() const { return _activePortal.active ? PortalRankName(_activePortal.rankIndex) : "-"; }
        float GetPortalBonusRadius() const { return MPLUS_PORTAL_BONUS_RADIUS; }

        float GetDistanceToActivePortal(Player const* player) const
        {
            if (!_activePortal.active || !player)
                return -1.0f;

            if (player->GetMapId() != _activePortal.mapId)
                return -1.0f;

            return player->GetDistance(
                _activePortal.pos.GetPositionX(),
                _activePortal.pos.GetPositionY(),
                _activePortal.pos.GetPositionZ()
            );
        }

        uint32 GetActivePortalBonusPctForPlayer(Player const* player) const
        {
            if (!_activePortal.active || !player)
                return 0;

            if (player->GetMapId() != _activePortal.mapId)
                return 0;

            float distance = GetDistanceToActivePortal(player);
            if (distance < 0.0f || distance > MPLUS_PORTAL_BONUS_RADIUS)
                return 0;

            return _activePortal.bonusPct;
        }

        uint32 GetActivePortalSecondsLeft() const
        {
            if (!_activePortal.active)
                return 0;
            std::time_t now = std::time(nullptr);
            if (_activePortal.expiresUnix <= now)
                return 0;
            return uint32(_activePortal.expiresUnix - now);
        }

        ActiveRun const* GetActiveRun(uint32 instanceId) const
        {
            auto itr = _activeRuns.find(instanceId);
            if (itr == _activeRuns.end())
                return nullptr;
            return &itr->second;
        }

        ActiveRun* GetActiveRun(uint32 instanceId)
        {
            auto itr = _activeRuns.find(instanceId);
            if (itr == _activeRuns.end())
                return nullptr;
            return &itr->second;
        }

        void LoadFromDb()
        {
            _dungeons.clear();
            _weeklyAffixMask = 0;
            _portalSpawns.clear();

            QueryResult dungeons = WorldDatabase.Query(
                "SELECT dungeon_id, name, map_id, final_boss_entry, enabled "
                "FROM custom_mplus_dungeon"
            );

            if (dungeons)
            {
                do
                {
                    Field* fields = dungeons->Fetch();
                    DungeonDef def;
                    def.dungeonId = fields[0].GetUInt16();
                    def.name = fields[1].GetString();
                    def.mapId = fields[2].GetUInt16();
                    def.finalBossEntry = fields[3].GetUInt32();
                    def.enabled = fields[4].GetUInt8() != 0;
                    _dungeons.push_back(std::move(def));
                }
                while (dungeons->NextRow());
            }

            QueryResult weekly = WorldDatabase.Query(
                "SELECT affix_mask FROM custom_mplus_weekly WHERE id = 1"
            );
            if (weekly)
                _weeklyAffixMask = weekly->Fetch()[0].GetUInt32();

            QueryResult portalSpawns = WorldDatabase.Query(
                "SELECT spawn_id, name, map_id, position_x, position_y, position_z, orientation "
                "FROM custom_mplus_world_portal_spawn WHERE enabled = 1"
            );
            if (portalSpawns)
            {
                do
                {
                    Field* fields = portalSpawns->Fetch();
                    PortalSpawnDef s;
                    s.spawnId = fields[0].GetUInt32();
                    s.name = fields[1].GetString();
                    s.mapId = fields[2].GetUInt16();
                    s.pos.Relocate(fields[3].GetFloat(), fields[4].GetFloat(), fields[5].GetFloat(), fields[6].GetFloat());
                    _portalSpawns.push_back(std::move(s));
                }
                while (portalSpawns->NextRow());
            }

            std::time_t now = std::time(nullptr);
            QueryResult portalState = WorldDatabase.Query(
                "SELECT next_spawn_unix FROM custom_mplus_world_portal_state WHERE id = 1"
            );
            if (portalState)
                _nextPortalSpawnUnix = portalState->Fetch()[0].GetUInt32();
            bool resetSpawnTimer = false;
            if (!_nextPortalSpawnUnix || _nextPortalSpawnUnix < uint32(now))
            {
                _nextPortalSpawnUnix = uint32(now + MPLUS_PORTAL_SPAWN_INTERVAL_SECONDS);
                resetSpawnTimer = true;
            }
            if (resetSpawnTimer)
                PersistPortalNextSpawn();

            _loaded = true;

            TC_LOG_INFO(
                "server.loading",
                "Mythic+ MVP: loaded {} dungeons, {} portal spawns, weekly affix mask={}.",
                uint32(_dungeons.size()),
                uint32(_portalSpawns.size()),
                _weeklyAffixMask
            );
        }

        bool AssignRandomKeyToPlayer(Player* player)
        {
            EnsureLoaded();
            DungeonDef const* dungeon = GetRandomEnabledDungeon();
            if (!dungeon)
                return false;

            uint32 affixMask = GetWeeklyAffixMask();
            uint8 level = 2;

            CharacterDatabase.PExecute(
                "REPLACE INTO custom_mplus_player_key (guid, dungeon_id, level, affix_mask) "
                "VALUES (%u, %u, %u, %u)",
                player->GetGUID().GetCounter(),
                uint32(dungeon->dungeonId),
                uint32(level),
                affixMask
            );
            return true;
        }

        void TryRollWorldKeystoneDrop(Player* killer, Creature* killed)
        {
            if (!killer || !killed)
                return;

            Map* map = killer->GetMap();
            if (!map || map->IsDungeon() || map->IsRaid())
                return; // open-world only for this feature

            uint32 localBonus = GetActivePortalBonusPctForPlayer(killer);
            float chance = MPLUS_BASE_KEY_DROP_CHANCE + float(localBonus);
            chance = std::min(chance, 95.0f);

            if (!roll_chance_f(chance))
                return;

            killer->AddItem(MPLUS_KEYSTONE_ITEM_ENTRY, 1);
            ChatHandler(killer->GetSession()).PSendSysMessage(
                "Мифик+: выпал Keystone! Шанс был %.1f%% (бонус рядом с порталом: +%u%%, радиус %.0f м).",
                chance,
                localBonus,
                MPLUS_PORTAL_BONUS_RADIUS
            );
        }

        bool SetPlayerKey(Player* player, uint16 dungeonId, uint8 level)
        {
            EnsureLoaded();
            DungeonDef const* dungeon = GetDungeonById(dungeonId);
            if (!dungeon || !dungeon->enabled)
                return false;

            uint32 affixMask = GetWeeklyAffixMask();
            CharacterDatabase.PExecute(
                "REPLACE INTO custom_mplus_player_key (guid, dungeon_id, level, affix_mask) "
                "VALUES (%u, %u, %u, %u)",
                player->GetGUID().GetCounter(),
                uint32(dungeonId),
                uint32(level),
                affixMask
            );
            return true;
        }

        bool GetPlayerKey(Player* player, uint16& outDungeonId, uint8& outLevel, uint32& outAffixMask) const
        {
            QueryResult key = CharacterDatabase.PQuery(
                "SELECT dungeon_id, level, affix_mask "
                "FROM custom_mplus_player_key WHERE guid = %u",
                player->GetGUID().GetCounter()
            );
            if (!key)
                return false;

            Field* fields = key->Fetch();
            outDungeonId = fields[0].GetUInt16();
            outLevel = fields[1].GetUInt8();
            outAffixMask = fields[2].GetUInt32();
            return true;
        }

        bool StartRun(Player* player, DungeonDef const* dungeon, uint8 level, uint32 affixMask, bool requireToken)
        {
            if (!player || !dungeon)
                return false;

            Map* map = player->GetMap();
            if (!map || !map->IsDungeon() || map->IsRaid())
                return false;

            Group* group = player->GetGroup();
            if (group && group->GetLeaderGUID() != player->GetGUID())
                return false;

            uint32 instanceId = map->GetInstanceId();
            if (GetActiveRun(instanceId))
                return false; // already running

            if (requireToken && !player->HasItemCount(MPLUS_KEYSTONE_ITEM_ENTRY, 1, true))
                return false; // no token => no Mythic+

            if (dungeon->mapId != map->GetId())
            {
                ChatHandler(player->GetSession()).PSendSysMessage(
                    "Мифик+: твой ключ для '%s', но ты вошёл в другое подземелье.",
                    dungeon->name.c_str()
                );
                return false;
            }

            ActiveRun run;
            run.instanceId = instanceId;
            run.mapId = dungeon->mapId;
            run.dungeonId = dungeon->dungeonId;
            run.finalBossEntry = dungeon->finalBossEntry;
            run.dungeonName = dungeon->name;
            run.leaderGuid = player->GetGUID().GetCounter();
            run.level = std::max<uint8>(2, level);
            run.affixMask = affixMask;
            run.startUnix = std::time(nullptr);

            _activeRuns.emplace(instanceId, run);

            // Announce to players currently in the instance.
            for (Map::PlayerList::const_iterator it = map->GetPlayers().begin(); it != map->GetPlayers().end(); ++it)
            {
                if (Player* p = it->GetSource())
                {
                    ChatHandler(p->GetSession()).PSendSysMessage(
                        "Мифик+ начат: %s +%u (аффиксы: %u).",
                        run.dungeonName.c_str(),
                        uint32(run.level),
                        run.affixMask
                    );
                }
            }

            return true;
        }

        bool TryStartRunOnMapEnter(Player* player)
        {
            EnsureLoaded();

            uint16 dungeonId = 0;
            uint8 level = 0;
            uint32 affixMask = 0;
            if (!GetPlayerKey(player, dungeonId, level, affixMask))
                return false;

            DungeonDef const* dungeon = GetDungeonById(dungeonId);
            if (!dungeon || !dungeon->enabled)
                return false;

            return StartRun(player, dungeon, level, affixMask, /*requireToken=*/true);
        }

        bool TryStartRunManual(Player* player)
        {
            EnsureLoaded();

            Map* map = player ? player->GetMap() : nullptr;
            if (!map || !map->IsDungeon() || map->IsRaid())
                return false;

            DungeonDef const* currentDungeon = nullptr;
            for (DungeonDef const& d : _dungeons)
            {
                if (d.enabled && d.mapId == map->GetId())
                {
                    currentDungeon = &d;
                    break;
                }
            }
            if (!currentDungeon)
                return false;

            uint16 dungeonId = 0;
            uint8 level = 2;
            uint32 affixMask = GetWeeklyAffixMask();

            if (GetPlayerKey(player, dungeonId, level, affixMask))
            {
                if (dungeonId != currentDungeon->dungeonId)
                    dungeonId = currentDungeon->dungeonId;
            }
            else
            {
                SetPlayerKey(player, currentDungeon->dungeonId, level);
            }

            return StartRun(player, currentDungeon, std::max<uint8>(2, level), affixMask, /*requireToken=*/false);
        }

        void OnPlayerKilledByCreature(Player* killed)
        {
            Map* map = killed->GetMap();
            if (!map || !map->IsDungeon() || map->IsRaid())
                return;

            ActiveRun* run = GetActiveRun(map->GetInstanceId());
            if (!run || run->completed)
                return;

            ++run->deaths;
        }

        void OnCreatureKilledByPlayer(Player* killer, Creature* killed)
        {
            Map* map = killer->GetMap();
            if (!map || !map->IsDungeon() || map->IsRaid())
                return;

            ActiveRun* run = GetActiveRun(map->GetInstanceId());
            if (!run || run->completed)
                return;

            if (!run->finalBossEntry)
                return;

            if (killed->GetEntry() != run->finalBossEntry)
                return;

            FinishRun(*run, /*success=*/true, map);
            _activeRuns.erase(map->GetInstanceId());
        }

        float GetEffectiveHealthMultiplier(uint8 level, uint32 affixMask, Creature const* victim) const
        {
            if (level < 2)
                level = 2;

            float base = 1.0f + 0.12f * float(level - 2);
            bool isBoss = victim && victim->IsDungeonBoss();

            if (isBoss && (affixMask & MPLUS_AFFIX_TYRANNICAL))
                base *= 1.20f;
            else if (!isBoss && (affixMask & MPLUS_AFFIX_FORTIFIED))
                base *= 1.20f;

            return std::max(1.0f, base);
        }

        float GetCreatureDamageMultiplier(uint8 level, uint32 affixMask, Creature const* attacker) const
        {
            if (level < 2)
                level = 2;

            float base = 1.0f + 0.10f * float(level - 2);
            bool isBoss = attacker && attacker->IsDungeonBoss();

            if (isBoss && (affixMask & MPLUS_AFFIX_TYRANNICAL))
                base *= 1.15f;
            else if (!isBoss && (affixMask & MPLUS_AFFIX_FORTIFIED))
                base *= 1.15f;

            return std::max(1.0f, base);
        }

    private:
        MythicPlusMgr() = default;

        void PersistPortalNextSpawn() const
        {
            WorldDatabase.PExecute(
                "REPLACE INTO custom_mplus_world_portal_state (id, next_spawn_unix) VALUES (1, %u)",
                _nextPortalSpawnUnix
            );
        }

        void BroadcastPortalMessage(std::string const& message) const
        {
            for (auto const& pair : sWorld->GetAllSessions())
            {
                if (WorldSession* session = pair.second)
                    ChatHandler(session).SendSysMessage(message.c_str());
            }
        }

        void DespawnActivePortal(bool announce)
        {
            if (_activePortal.active && !_activePortal.portalGuid.IsEmpty())
            {
                if (Map* map = MapManager::instance()->CreateBaseMap(_activePortal.mapId))
                    if (Creature* gate = map->GetCreature(_activePortal.portalGuid))
                        gate->DespawnOrUnsummon();
            }

            if (announce && _activePortal.active)
            {
                BroadcastPortalMessage(std::string("Мировые Врата ") + PortalRankName(_activePortal.rankIndex) + " закрылись.");
            }

            _activePortal = ActivePortal{};
        }

        void SpawnRandomPortal(std::time_t now)
        {
            if (_portalSpawns.empty())
            {
                _nextPortalSpawnUnix = uint32(now + MPLUS_PORTAL_SPAWN_INTERVAL_SECONDS);
                PersistPortalNextSpawn();
                return;
            }

            PortalSpawnDef const& spawn = _portalSpawns[urand(0u, uint32(_portalSpawns.size() - 1))];
            uint8 rank = uint8(urand(0u, 6u)); // F..S
            uint32 bonus = uint32((rank + 1) * 10u);

            Creature* summonedGate = nullptr;
            if (Map* map = MapManager::instance()->CreateBaseMap(spawn.mapId))
            {
                summonedGate = map->SummonCreature(
                    MPLUS_WORLD_GATE_ENTRY,
                    spawn.pos,
                    nullptr,
                    MPLUS_PORTAL_ACTIVE_SECONDS * 1000u
                );
            }

            _activePortal.active = true;
            _activePortal.spawnId = spawn.spawnId;
            _activePortal.mapId = spawn.mapId;
            _activePortal.pos = spawn.pos;
            _activePortal.rankIndex = rank;
            _activePortal.bonusPct = bonus;
            _activePortal.expiresUnix = now + MPLUS_PORTAL_ACTIVE_SECONDS;
            _activePortal.portalGuid = summonedGate ? summonedGate->GetGUID() : ObjectGuid::Empty;

            _nextPortalSpawnUnix = uint32(now + MPLUS_PORTAL_SPAWN_INTERVAL_SECONDS);
            PersistPortalNextSpawn();

            BroadcastPortalMessage(
                std::string("Открылись Мировые Врата ранга ") +
                PortalRankName(rank) +
                "! Бонус к шансу дропа Mythic Keystone: +" +
                std::to_string(bonus) +
                "% (до закрытия: " +
                std::to_string(MPLUS_PORTAL_ACTIVE_SECONDS / 60) +
                " мин)."
            );
        }

        void HandleWorldPortalTick()
        {
            std::time_t now = std::time(nullptr);

            if (_activePortal.active && _activePortal.expiresUnix <= now)
                DespawnActivePortal(/*announce=*/true);

            if (!_activePortal.active && _nextPortalSpawnUnix && now >= std::time_t(_nextPortalSpawnUnix))
                SpawnRandomPortal(now);
        }

        void PersistRunHistory(ActiveRun const& run, bool success, std::time_t endUnix) const
        {
            if (!run.startUnix)
                return;

            uint32 start = uint32(run.startUnix);
            uint32 end = endUnix ? uint32(endUnix) : uint32(std::time(nullptr));
            uint32 durationMs = end > start ? (end - start) * 1000u : 0u;

            CharacterDatabase.PExecute(
                "INSERT INTO custom_mplus_run_history "
                "(leader_guid, dungeon_id, map_id, instance_id, level, affix_mask, start_unix, end_unix, duration_ms, deaths, success) "
                "VALUES (%u, %u, %u, %u, %u, %u, %u, %u, %u, %u, %u)",
                run.leaderGuid,
                uint32(run.dungeonId),
                uint32(run.mapId),
                run.instanceId,
                uint32(run.level),
                run.affixMask,
                start,
                end,
                durationMs,
                uint32(run.deaths),
                success ? 1u : 0u
            );
        }

        void FinishRun(ActiveRun& run, bool success, Map* map)
        {
            if (run.completed)
                return;

            std::time_t endUnix = std::time(nullptr);
            run.completed = true;

            PersistRunHistory(run, success, endUnix);

            uint32 rewardCount = 1 + uint32(run.level) / 5;
            rewardCount = std::min<uint32>(rewardCount, 5u);

            for (Map::PlayerList::const_iterator it = map->GetPlayers().begin(); it != map->GetPlayers().end(); ++it)
            {
                if (Player* p = it->GetSource())
                {
                    p->AddItem(MPLUS_REWARD_ITEM_ENTRY, rewardCount);
                    ChatHandler(p->GetSession()).PSendSysMessage(
                        "Мифик+ завершён! Награда: %u x предмет %u.",
                        rewardCount,
                        MPLUS_REWARD_ITEM_ENTRY
                    );
                }
            }

            // Upgrade key for leader.
            if (Player* leader = ObjectAccessor::FindConnectedPlayer(ObjectGuid::Create<HighGuid::Player>(run.leaderGuid)))
            {
                uint16 nextDungeonId = run.dungeonId;
                uint8 nextLevel = run.level;

                if (success)
                    nextLevel = std::min<uint8>(uint8(nextLevel + 1), 25);
                else if (nextLevel > 2)
                    nextLevel = uint8(nextLevel - 1);

                if (DungeonDef const* rnd = GetRandomEnabledDungeon())
                    nextDungeonId = rnd->dungeonId;

                SetPlayerKey(leader, nextDungeonId, nextLevel);
                ChatHandler(leader->GetSession()).PSendSysMessage("Твой ключ обновлён: уровень %u.", uint32(nextLevel));
            }
        }

        std::vector<DungeonDef> _dungeons;
        uint32 _weeklyAffixMask = 0;
        std::vector<PortalSpawnDef> _portalSpawns;
        ActivePortal _activePortal;
        uint32 _nextPortalSpawnUnix = 0;

        std::unordered_map<uint32, ActiveRun> _activeRuns;

        uint32 _cleanupTimer = 0;
        bool _loaded = false;
    };

    class mplus_worldscript : public WorldScript
    {
    public:
        mplus_worldscript() : WorldScript("mplus_worldscript") { }

        void OnStartup() override
        {
            MythicPlusMgr::Instance().LoadFromDb();
        }

        void OnUpdate(uint32 diff) override
        {
            MythicPlusMgr::Instance().Update(diff);
        }
    };

    class mplus_playerscript : public PlayerScript
    {
    public:
        mplus_playerscript() : PlayerScript("mplus_playerscript") { }

        void OnMapChanged(Player* player) override
        {
            MythicPlusMgr::Instance().TryStartRunOnMapEnter(player);
        }

        void OnCreatureKill(Player* killer, Creature* killed) override
        {
            MythicPlusMgr::Instance().OnCreatureKilledByPlayer(killer, killed);
            MythicPlusMgr::Instance().TryRollWorldKeystoneDrop(killer, killed);
        }

        void OnPlayerKilledByCreature(Creature* /*killer*/, Player* killed) override
        {
            MythicPlusMgr::Instance().OnPlayerKilledByCreature(killed);
        }
    };

    class mplus_unitscript : public UnitScript
    {
    public:
        mplus_unitscript() : UnitScript("mplus_unitscript") { }

        void OnDamage(Unit* attacker, Unit* victim, uint32& damage) override
        {
            if (!attacker || !victim || !damage)
                return;

            Map* map = attacker->GetMap();
            if (!map || !map->IsDungeon() || map->IsRaid())
                return;

            ActiveRun const* run = MythicPlusMgr::Instance().GetActiveRun(map->GetInstanceId());
            if (!run || run->completed)
                return;

            // Player-controlled attackers (players, pets, guardians) => reduce damage to NPCs (effective HP increase).
            Player* controllingPlayer = attacker->GetCharmerOrOwnerPlayerOrPlayerItself();
            bool attackerIsPlayerControlled = controllingPlayer != nullptr;

            bool victimIsCreature = victim->GetTypeId() == TYPEID_UNIT && !victim->GetCharmerOrOwnerPlayerOrPlayerItself();
            bool victimIsPlayerControlled = victim->GetCharmerOrOwnerPlayerOrPlayerItself() != nullptr;

            if (attackerIsPlayerControlled && victimIsCreature)
            {
                Creature const* v = victim->ToCreature();
                float hpMult = MythicPlusMgr::Instance().GetEffectiveHealthMultiplier(run->level, run->affixMask, v);
                uint32 newDamage = uint32(float(damage) / hpMult);
                damage = std::max<uint32>(1u, newDamage);
            }
            else if (!attackerIsPlayerControlled && attacker->GetTypeId() == TYPEID_UNIT && victimIsPlayerControlled)
            {
                Creature const* a = attacker->ToCreature();
                float dmgMult = MythicPlusMgr::Instance().GetCreatureDamageMultiplier(run->level, run->affixMask, a);
                uint32 newDamage = uint32(float(damage) * dmgMult);
                damage = std::max<uint32>(1u, newDamage);
            }
        }
    };

    class npc_mplus_keystone_master : public CreatureScript
    {
    public:
        npc_mplus_keystone_master() : CreatureScript("npc_mplus_keystone_master") { }

        struct npc_mplus_keystone_masterAI : public ScriptedAI
        {
            npc_mplus_keystone_masterAI(Creature* creature) : ScriptedAI(creature) { }

            bool OnGossipHello(Player* player) override
            {
                MythicPlusMgr::Instance().EnsureLoaded();

                ClearGossipMenuFor(player);

                uint16 dungeonId = 0;
                uint8 level = 0;
                uint32 affixMask = 0;
                bool hasKey = MythicPlusMgr::Instance().GetPlayerKey(player, dungeonId, level, affixMask);

                if (hasKey)
                {
                    if (DungeonDef const* dungeon = MythicPlusMgr::Instance().GetDungeonById(dungeonId))
                    {
                        AddGossipItemFor(
                            player,
                            GOSSIP_ICON_CHAT,
                            "Текущий ключ: " + dungeon->name + " +" + std::to_string(uint32(level)) + " (аффиксы: " + std::to_string(affixMask) + ")",
                            GOSSIP_SENDER_MAIN,
                            100
                        );
                    }
                }
                else
                {
                    AddGossipItemFor(player, GOSSIP_ICON_CHAT, "У тебя нет активного ключа.", GOSSIP_SENDER_MAIN, 100);
                }

                AddGossipItemFor(player, GOSSIP_ICON_CHAT, "Выдать предмет-ключ (камень)", GOSSIP_SENDER_MAIN, 1);
                AddGossipItemFor(player, GOSSIP_ICON_CHAT, "Сгенерировать новый ключ (рандом)", GOSSIP_SENDER_MAIN, 2);
                AddGossipItemFor(player, GOSSIP_ICON_CHAT, "Выбрать подземелье для ключа", GOSSIP_SENDER_MAIN, 3);
                AddGossipItemFor(player, GOSSIP_ICON_CHAT, "Запустить М+ в текущем инсте (тест/соло)", GOSSIP_SENDER_MAIN, 4);
                AddGossipItemFor(player, GOSSIP_ICON_CHAT, "Статус мировых врат", GOSSIP_SENDER_MAIN, 5);

                SendGossipMenuFor(player, DEFAULT_GOSSIP_MESSAGE, me);
                return true;
            }

            bool OnGossipSelect(Player* player, uint32 /*menuId*/, uint32 gossipListId) override
            {
                uint32 action = GetGossipActionFor(player, gossipListId);

                if (action == 1)
                {
                    CloseGossipMenuFor(player);
                    if (!player->HasItemCount(MPLUS_KEYSTONE_ITEM_ENTRY, 1, true))
                        player->AddItem(MPLUS_KEYSTONE_ITEM_ENTRY, 1);

                    ChatHandler(player->GetSession()).SendSysMessage("Мифик+: предмет-ключ выдан.");
                    return true;
                }

                if (action == 2)
                {
                    CloseGossipMenuFor(player);

                    if (!player->HasItemCount(MPLUS_KEYSTONE_ITEM_ENTRY, 1, true))
                        player->AddItem(MPLUS_KEYSTONE_ITEM_ENTRY, 1);

                    if (!MythicPlusMgr::Instance().AssignRandomKeyToPlayer(player))
                    {
                        ChatHandler(player->GetSession()).SendSysMessage("Мифик+: нет доступных подземелий (проверь custom_mplus_dungeon).");
                        return true;
                    }

                    ChatHandler(player->GetSession()).SendSysMessage("Мифик+: новый ключ создан. Заходи в нужное подземелье с камнем в сумке.");
                    return true;
                }

                if (action == 3)
                {
                    ClearGossipMenuFor(player);

                    for (DungeonDef const& d : MythicPlusMgr::Instance().GetDungeons())
                    {
                        if (!d.enabled)
                            continue;
                        AddGossipItemFor(
                            player,
                            GOSSIP_ICON_CHAT,
                            d.name,
                            GOSSIP_SENDER_MAIN,
                            1000 + d.dungeonId
                        );
                    }
                    AddGossipItemFor(player, GOSSIP_ICON_CHAT, "Назад", GOSSIP_SENDER_MAIN, 999);
                    SendGossipMenuFor(player, DEFAULT_GOSSIP_MESSAGE, me);
                    return true;
                }

                if (action == 4)
                {
                    CloseGossipMenuFor(player);
                    if (!MythicPlusMgr::Instance().TryStartRunManual(player))
                    {
                        ChatHandler(player->GetSession()).SendSysMessage(
                            "Мифик+: ручной запуск не удался. Нужен 5p инстанс из custom_mplus_dungeon."
                        );
                        return true;
                    }
                    ChatHandler(player->GetSession()).SendSysMessage("Мифик+: ручной запуск выполнен.");
                    return true;
                }

                if (action == 5)
                {
                    CloseGossipMenuFor(player);
                    if (!MythicPlusMgr::Instance().HasActivePortal())
                    {
                        ChatHandler(player->GetSession()).SendSysMessage("Мировые Врата сейчас закрыты.");
                        return true;
                    }

                    uint32 localBonus = MythicPlusMgr::Instance().GetActivePortalBonusPctForPlayer(player);
                    float distance = MythicPlusMgr::Instance().GetDistanceToActivePortal(player);
                    char const* mapState = distance < 0.0f ? "другая карта" : "та же карта";
                    float shownDistance = distance < 0.0f ? 0.0f : distance;

                    ChatHandler(player->GetSession()).PSendSysMessage(
                        "Активные Врата: ранг %s, общий бонус %u%%, твой бонус сейчас +%u%% (радиус %.0f м), расстояние %.1f м (%s), осталось %u сек, карта %u.",
                        MythicPlusMgr::Instance().GetActivePortalRankName(),
                        MythicPlusMgr::Instance().GetActivePortalBonusPct(),
                        localBonus,
                        MythicPlusMgr::Instance().GetPortalBonusRadius(),
                        shownDistance,
                        mapState,
                        MythicPlusMgr::Instance().GetActivePortalSecondsLeft(),
                        MythicPlusMgr::Instance().GetActivePortalMapId()
                    );
                    return true;
                }

                if (action == 999)
                    return OnGossipHello(player);

                if (action >= 1000 && action < 2000)
                {
                    uint16 dungeonId = uint16(action - 1000);

                    uint16 curDungeonId = dungeonId;
                    uint8 curLevel = 2;
                    uint32 curAffix = 0;
                    MythicPlusMgr::Instance().GetPlayerKey(player, curDungeonId, curLevel, curAffix);

                    if (!player->HasItemCount(MPLUS_KEYSTONE_ITEM_ENTRY, 1, true))
                        player->AddItem(MPLUS_KEYSTONE_ITEM_ENTRY, 1);

                    if (!MythicPlusMgr::Instance().SetPlayerKey(player, dungeonId, std::max<uint8>(2, curLevel)))
                    {
                        ChatHandler(player->GetSession()).SendSysMessage("Мифик+: не удалось установить подземелье.");
                        CloseGossipMenuFor(player);
                        return true;
                    }

                    if (DungeonDef const* dungeon = MythicPlusMgr::Instance().GetDungeonById(dungeonId))
                        ChatHandler(player->GetSession()).PSendSysMessage("Мифик+: ключ установлен на '%s'.", dungeon->name.c_str());

                    CloseGossipMenuFor(player);
                    return true;
                }

                // No-op for info row.
                return true;
            }
        };

        CreatureAI* GetAI(Creature* creature) const override
        {
            return new npc_mplus_keystone_masterAI(creature);
        }
    };
} // namespace

void AddSC_custom_mythic_plus_mvp()
{
    new mplus_worldscript();
    new mplus_playerscript();
    new mplus_unitscript();
    new npc_mplus_keystone_master();
}


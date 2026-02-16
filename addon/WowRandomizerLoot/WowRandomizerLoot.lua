local TITLE = "|cff66d9ffRandomizer Loot|r"
local MAX_LINES = 12

WRL_LootByCreature = WRL_LootByCreature or {}
WRL_CreatureNames = WRL_CreatureNames or {}
WRL_ItemNames = WRL_ItemNames or {}
WRL_Metadata = WRL_Metadata or {}

local function GetNpcIdFromGUID(guid)
    if not guid then
        return nil
    end
    local unitType, _, _, _, _, npcId = strsplit("-", guid)
    if unitType ~= "Creature" and unitType ~= "Vehicle" then
        return nil
    end
    return tonumber(npcId)
end

local function BuildItemLabel(itemId)
    local itemLink = select(2, GetItemInfo(itemId))
    if itemLink then
        return itemLink
    end
    local fallbackName = WRL_ItemNames[itemId]
    if fallbackName and fallbackName ~= "" then
        return string.format("|cffb0b0b0%s|r", fallbackName)
    end
    return string.format("|cffb0b0b0Item #%d|r", itemId)
end

local function FormatLootRightText(loot)
    local chance = tonumber(loot.chance) or 0
    local minCount = tonumber(loot.min) or 1
    local maxCount = tonumber(loot.max) or minCount
    if minCount < 1 then
        minCount = 1
    end
    if maxCount < minCount then
        maxCount = minCount
    end

    local countText = ""
    if minCount == maxCount and minCount > 1 then
        countText = string.format(" x%d", minCount)
    elseif minCount ~= maxCount then
        countText = string.format(" x%d-%d", minCount, maxCount)
    end

    local chanceText = string.format("%.2f%%", chance)
    if loot.quest then
        chanceText = chanceText .. " Q"
    end
    return chanceText .. countText
end

local function AddLootToTooltip(tooltip, unit)
    if not tooltip or not unit then
        return
    end
    if UnitIsPlayer(unit) then
        return
    end

    local npcId = GetNpcIdFromGUID(UnitGUID(unit))
    if not npcId then
        return
    end

    local lootList = WRL_LootByCreature[npcId]
    if not lootList or #lootList == 0 then
        return
    end

    local creatureName = WRL_CreatureNames[npcId]
    tooltip:AddLine(" ")
    if creatureName and creatureName ~= "" then
        tooltip:AddLine(TITLE .. " - " .. creatureName, 0.4, 0.85, 1.0)
    else
        tooltip:AddLine(TITLE, 0.4, 0.85, 1.0)
    end

    local maxLines = MAX_LINES
    if #lootList < maxLines then
        maxLines = #lootList
    end

    for i = 1, maxLines do
        local loot = lootList[i]
        local itemId = tonumber(loot.item)
        if itemId then
            local left = BuildItemLabel(itemId)
            local right = FormatLootRightText(loot)
            tooltip:AddDoubleLine(left, right, 0.95, 0.95, 0.95, 0.35, 0.75, 1.0)
        end
    end

    if #lootList > maxLines then
        tooltip:AddLine(string.format("... and %d more", #lootList - maxLines), 0.5, 0.8, 1.0)
    end
    tooltip:Show()
end

GameTooltip:HookScript("OnTooltipSetUnit", function(tooltip)
    local _, unit = tooltip:GetUnit()
    AddLootToTooltip(tooltip, unit)
end)

SLASH_WOWRANDOMIZERLOOT1 = "/wrl"
SlashCmdList["WOWRANDOMIZERLOOT"] = function()
    local creatureCount = 0
    for _ in pairs(WRL_LootByCreature) do
        creatureCount = creatureCount + 1
    end
    local generatedAt = WRL_Metadata.generated_at or 0
    DEFAULT_CHAT_FRAME:AddMessage(
        string.format(
            "|cff66d9ff[WRL]|r creatures: %d, generated_at: %s",
            creatureCount,
            tostring(generatedAt)
        )
    )
end

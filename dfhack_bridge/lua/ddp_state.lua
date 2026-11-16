-- Dwarf Doctrine Pilot: read-only fortress state exporter.
-- Usage: dfhack-run lua -f <path>/ddp_state.lua [<output.json>]
-- Exports RAW game facts (DF enum names, tile counts); the mapping to the
-- DDP FortressState lives in dfhack_bridge/dfhack_state.py so it can be unit-tested.
-- Every section runs under pcall: a failing section is reported in `errors`
-- instead of aborting the whole export. Never modifies the game.

local SCHEMA = 1
local t0 = os.clock()
local args = {...}
local out_path = args[1]

-- ---------------------------------------------------------------- JSON
local ARRAY_MT = {}
local function arr(t) return setmetatable(t or {}, ARRAY_MT) end

local function esc(s)
    return (s:gsub('[%c"\\]', function(c)
        local map = {['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t'}
        return map[c] or string.format('\\u%04x', c:byte())
    end))
end

local function encode(v, buf)
    local t = type(v)
    if t == 'nil' then buf[#buf + 1] = 'null'
    elseif t == 'boolean' then buf[#buf + 1] = tostring(v)
    elseif t == 'number' then
        if v ~= v or v == math.huge or v == -math.huge then buf[#buf + 1] = 'null'
        elseif math.type(v) == 'integer' then buf[#buf + 1] = string.format('%d', v)
        else buf[#buf + 1] = string.format('%.6g', v) end
    elseif t == 'string' then buf[#buf + 1] = '"' .. esc(v) .. '"'
    elseif t == 'table' then
        if getmetatable(v) == ARRAY_MT then
            buf[#buf + 1] = '['
            for i, x in ipairs(v) do
                if i > 1 then buf[#buf + 1] = ',' end
                encode(x, buf)
            end
            buf[#buf + 1] = ']'
        else
            local keys, vals = {}, {}
            for k, x in pairs(v) do
                local ks = tostring(k)
                keys[#keys + 1] = ks
                vals[ks] = x
            end
            table.sort(keys)
            buf[#buf + 1] = '{'
            for i, k in ipairs(keys) do
                if i > 1 then buf[#buf + 1] = ',' end
                buf[#buf + 1] = '"' .. esc(k) .. '":'
                encode(vals[k], buf)
            end
            buf[#buf + 1] = '}'
        end
    else buf[#buf + 1] = 'null' end
end

local function to_json(v)
    local buf = {}
    encode(v, buf)
    return table.concat(buf)
end

-- ---------------------------------------------------------------- helpers
local function utf(s) return s and dfhack.df2utf(s) or '' end
local function enum_name(enum, v) return enum[v] or tostring(v) end

-- Items that count as usable fortress stock (same exclusions as DFHack's workflow
-- plugin). Not `foreign`: embark supplies carry it too.
local function usable(item)
    local f = item.flags
    return not (f.forbid or f.dump or f.rotten or f.trader or f.hostile or f.on_fire
        or f.removed or f.garbage_collect or f.construction or f.in_building or f.artifact)
end

local STOCK_TYPES = {'DRINK', 'FOOD', 'PLANT', 'PLANT_GROWTH', 'MEAT', 'FISH', 'WOOD', 'BOULDER',
    'BLOCKS', 'BAR', 'BED', 'BARREL', 'BIN', 'TABLE', 'CHAIR', 'DOOR'}

local LABORS = {'MINE', 'CUTWOOD', 'CARPENTER', 'MASON', 'BREWER', 'COOK', 'PLANT', 'FISH', 'HUNT',
    'FORGE_WEAPON', 'FORGE_ARMOR', 'FORGE_FURNITURE', 'METAL_CRAFT', 'HAUL_STONE', 'HAUL_WOOD',
    'HAUL_ITEM', 'HAUL_FOOD', 'HAUL_FURNITURE'}

local SKILLS = {'MINING', 'WOODCUTTING', 'CARPENTRY', 'MASONRY', 'BREWING', 'COOK', 'PLANT', 'FISH',
    'SNEAK', 'FORGE_WEAPON', 'FORGE_ARMOR', 'FORGE_FURNITURE'}

local PILE_FLAGS = {'animals', 'food', 'furniture', 'corpses', 'refuse', 'stone', 'ammo', 'coins',
    'bars_blocks', 'gems', 'finished_goods', 'leather', 'cloth', 'wood', 'weapons', 'armor', 'sheet'}

local function any_true(vec)
    if not vec then return false end
    for _, v in ipairs(vec) do if v then return true end end
    return false
end

-- ---------------------------------------------------------------- sections
local W = df.global.world
local state = {schema = SCHEMA, errors = arr()}

local sections = {}

function sections.meta()
    state.dfhack_version = dfhack.getDFHackVersion()
    state.df_version = dfhack.getDFVersion()
    state.year = df.global.cur_year
    state.year_tick = df.global.cur_year_tick
    state.paused = df.global.pause_state and true or false
    local site = dfhack.world.getCurrentSite()
    state.fortress_name = site and utf(dfhack.translation.translateName(site.name, true)) or ''
end

function sections.stocks()
    local items, bars = {}, {}
    for _, tname in ipairs(STOCK_TYPES) do
        local n = 0
        for _, item in ipairs(W.items.other[tname]) do
            if usable(item) then
                local qty = item:getStackSize()
                n = n + qty
                if tname == 'BAR' then
                    local mi = dfhack.matinfo.decode(item)
                    local tok = mi and mi:getToken() or 'UNKNOWN'
                    bars[tok] = (bars[tok] or 0) + qty
                end
            end
        end
        items[tname] = n
    end
    state.items, state.bars = items, bars
end

function sections.stockpiles()
    local piles, tile_owner = arr(), {}
    for _, bld in ipairs(W.buildings.other.STOCKPILE) do
        local flags = arr()
        for _, fname in ipairs(PILE_FLAGS) do
            if bld.settings.flags[fname] then flags[#flags + 1] = fname end
        end
        local food = bld.settings.food
        local drink_only = bld.settings.flags.food and food ~= nil
            and (any_true(food.drink_plant) or any_true(food.drink_animal))
            and not (food.prepared_meals or any_true(food.meat) or any_true(food.fish)
                or any_true(food.plants) or any_true(food.leaves))
        local tiles = 0
        for x = bld.x1, bld.x2 do
            for y = bld.y1, bld.y2 do
                if dfhack.buildings.containsTile(bld, x, y) then
                    tiles = tiles + 1
                    tile_owner[x .. ',' .. y .. ',' .. bld.z] = #piles + 1
                end
            end
        end
        local give, take = arr(), arr()
        for _, ws in ipairs(bld.links.give_to_workshop) do give[#give + 1] = ws.id end
        for _, ws in ipairs(bld.links.take_from_workshop) do take[#take + 1] = ws.id end
        piles[#piles + 1] = {
            id = bld.id, number = bld.stockpile_number, name = utf(bld.name),
            x1 = bld.x1, y1 = bld.y1, x2 = bld.x2, y2 = bld.y2, z = bld.z,
            flags = flags, drink_only = drink_only and true or false,
            tiles = tiles, used_tiles = 0, give_to_workshops = give, take_from_workshops = take,
        }
    end
    state.stockpiles = piles
    -- One pass over loose items: occupancy of pile tiles + items lying outside piles.
    local used, ground = {}, {}
    for _, item in ipairs(W.items.other.IN_PLAY) do
        local f = item.flags
        if f.on_ground and not (f.in_building or f.construction or f.removed or f.spider_web
                or f.encased or f.trader or f.hostile or f.forbid or f.dump or f.garbage_collect) then
            local key = item.pos.x .. ',' .. item.pos.y .. ',' .. item.pos.z
            local pile = tile_owner[key]
            if pile then
                used[pile] = used[pile] or {}
                used[pile][key] = true
            else
                local tname = enum_name(df.item_type, item:getType())
                ground[tname] = (ground[tname] or 0) + 1
            end
        end
    end
    for idx, keys in pairs(used) do
        local n = 0
        for _ in pairs(keys) do n = n + 1 end
        piles[idx].used_tiles = n
    end
    state.ground = ground
end

function sections.workshops()
    local out = arr()
    for _, bld in ipairs(W.buildings.other.WORKSHOP_ANY) do
        out[#out + 1] = {id = bld.id, kind = 'workshop', type = enum_name(df.workshop_type, bld.type),
                         name = utf(bld.name), active = bld:getBuildStage() >= bld:getMaxBuildStage()}
    end
    for _, bld in ipairs(W.buildings.other.FURNACE_ANY) do
        out[#out + 1] = {id = bld.id, kind = 'furnace', type = enum_name(df.furnace_type, bld.type),
                         name = utf(bld.name), active = bld:getBuildStage() >= bld:getMaxBuildStage()}
    end
    state.workshops = out
end

function sections.zones()
    local out = arr()
    for _, bld in ipairs(W.buildings.other.ACTIVITY_ZONE) do
        local name = utf(bld.name)
        if name:upper():sub(1, 3) == 'DDP' then
            out[#out + 1] = {id = bld.id, name = name, type = enum_name(df.civzone_type, bld.type),
                             x1 = bld.x1, y1 = bld.y1, x2 = bld.x2, y2 = bld.y2, z = bld.z}
        end
    end
    state.zones = out
end

function sections.orders()
    local out = arr()
    for _, o in ipairs(W.manager_orders.all) do
        local cats = arr()
        for k, v in pairs(o.material_category) do if v then cats[#cats + 1] = tostring(k) end end
        table.sort(cats)
        out[#out + 1] = {
            id = o.id, job_type = enum_name(df.job_type, o.job_type), reaction = o.reaction_name,
            material_category = cats, amount_total = o.amount_total, amount_left = o.amount_left,
            active = o.status.active and true or false, validated = o.status.validated and true or false,
            frequency = enum_name(df.workquota_frequency_type, o.frequency),
        }
    end
    state.orders = out
end

function sections.units()
    local out = arr()
    for _, u in ipairs(dfhack.units.getCitizens()) do
        local soul = u.status.current_soul
        local labors = arr()
        for _, l in ipairs(LABORS) do
            if u.status.labors[df.unit_labor[l]] then labors[#labors + 1] = l end
        end
        local skills = {}
        for _, s in ipairs(SKILLS) do
            local lvl = dfhack.units.getNominalSkill(u, df.job_skill[s], true)
            if lvl and lvl > 0 then skills[s] = lvl end
        end
        local job = u.job.current_job
        out[#out + 1] = {
            id = u.id,
            name = utf(dfhack.translation.translateName(dfhack.units.getVisibleName(u), false)),
            profession = utf(dfhack.units.getProfessionName(u)),
            stress = soul and soul.personality.stress or 0,
            stress_category = dfhack.units.getStressCategory(u),
            job = job and utf(dfhack.job.getName(job)) or nil,
            labors = labors, skills = skills,
        }
    end
    state.units = out
end

function sections.threats()
    local invaders, great, danger, merchants = 0, 0, 0, 0
    for _, u in ipairs(W.units.active) do
        if dfhack.units.isActive(u) and not dfhack.units.isHidden(u) then
            if dfhack.units.isInvader(u) then invaders = invaders + 1 end
            if dfhack.units.isGreatDanger(u) then great = great + 1 end
            if dfhack.units.isDanger(u) then danger = danger + 1 end
            if dfhack.units.isMerchant(u) then merchants = merchants + 1 end
        end
    end
    state.threats = {invaders = invaders, great_dangers = great, dangers = danger, merchants = merchants}
end

local ORDER = {'meta', 'stocks', 'stockpiles', 'workshops', 'zones', 'orders', 'units', 'threats'}
for _, name in ipairs(ORDER) do
    local ok, err = pcall(sections[name])
    if not ok then state.errors[#state.errors + 1] = name .. ': ' .. tostring(err) end
end
state.elapsed_ms = math.floor((os.clock() - t0) * 1000)

local doc = to_json(state)
if out_path then
    local f = assert(io.open(out_path, 'w'))
    f:write(doc)
    f:close()
    print('ddp_state ok ' .. state.elapsed_ms .. 'ms errors=' .. #state.errors)
else
    print(doc)
end

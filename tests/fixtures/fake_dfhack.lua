-- Minimal stand-in for the DFHack Lua environment, enough to run ddp_state.lua
-- outside the game. Enums are identity maps: df.item_type.DRINK == 'DRINK'.
local function enum() return setmetatable({}, {__index = function(_, k) return k end}) end

local function item(t, qty, flags, extra)
    local it = {type = t, qty = qty or 1, flags = flags or {}, pos = {x = -30000, y = -30000, z = -30000}}
    for k, v in pairs(extra or {}) do it[k] = v end
    function it:getStackSize() return self.qty end
    function it:getType() return self.type end
    return it
end

local function bld(t)
    function t:getBuildStage() return self.stage or 1 end
    function t:getMaxBuildStage() return 1 end
    return t
end

local carpenter = bld{id = 10, type = 'Carpenters', name = ''}
local still = bld{id = 11, type = 'Still', name = ''}
local kitchen = bld{id = 12, type = 'Kitchen', name = '', stage = 0}
local smelter = bld{id = 13, type = 'Smelter', name = ''}

local function pile(t)
    t.links = t.links or {give_to_workshop = {}, take_from_workshop = {}}
    return bld(t)
end

local food_pile = pile{id = 100, stockpile_number = 1, name = '', x1 = 0, y1 = 0, x2 = 2, y2 = 1, z = 5,
    settings = {flags = {food = true}, food = {meat = {true}, fish = {}, plants = {true}, leaves = {},
        drink_plant = {true}, drink_animal = {}, prepared_meals = true}}}
local drink_pile = pile{id = 101, stockpile_number = 2, name = 'Cantina', x1 = 10, y1 = 0, x2 = 11, y2 = 0, z = 5,
    settings = {flags = {food = true}, food = {meat = {false}, fish = {}, plants = {false}, leaves = {},
        drink_plant = {true}, drink_animal = {true}, prepared_meals = false}},
    links = {give_to_workshop = {still}, take_from_workshop = {}}}
local wood_pile = pile{id = 102, stockpile_number = 3, name = '', x1 = 20, y1 = 0, x2 = 21, y2 = 1, z = 5,
    settings = {flags = {wood = true}}, links = {give_to_workshop = {carpenter}, take_from_workshop = {}}}

local on_ground = function(x, y) return {x = x, y = y, z = 5} end
local drink_on_pile = item('DRINK', 5, {on_ground = true}, {pos = on_ground(0, 0)})
local meal_on_pile = item('FOOD', 3, {on_ground = true}, {pos = on_ground(0, 0)})
local wood_on_pile = item('WOOD', 1, {on_ground = true}, {pos = on_ground(21, 1)})
local loose_wood = item('WOOD', 1, {on_ground = true}, {pos = on_ground(50, 50)})
local loose_stone = item('BOULDER', 1, {on_ground = true}, {pos = on_ground(51, 50)})
local forbidden_stone = item('BOULDER', 1, {on_ground = true, forbid = true}, {pos = on_ground(52, 50)})
local iron = item('BAR', 4, {}, {mat = 'INORGANIC:IRON'})
local copper = item('BAR', 2, {}, {mat = 'INORGANIC:COPPER'})
local rotten_meat = item('MEAT', 9, {rotten = true})

local other = setmetatable({
    DRINK = {drink_on_pile, item('DRINK', 15, {in_inventory = true, foreign = true})},  -- embark barrel
    FOOD = {meal_on_pile},
    PLANT = {item('PLANT', 7, {})},
    MEAT = {item('MEAT', 2, {}), rotten_meat},
    WOOD = {wood_on_pile, loose_wood},
    BOULDER = {loose_stone, forbidden_stone},
    BAR = {iron, copper},
    BED = {item('BED', 1, {in_building = true}), item('BED', 1, {})},
}, {__index = function() return {} end})
other.IN_PLAY = {drink_on_pile, meal_on_pile, wood_on_pile, loose_wood, loose_stone, forbidden_stone}

local function unit(id, name, opts)
    local labors = {}
    for _, l in ipairs(opts.labors or {}) do labors[l] = true end
    return {id = id, name = name, prof = opts.prof, skills = opts.skills or {}, job = {current_job = opts.job},
            status = {labors = labors, current_soul = {personality = {stress = opts.stress or 0}}},
            invader = opts.invader, hidden = opts.hidden, great = opts.great, merchant = opts.merchant}
end

local citizens = {
    unit(1, 'Urist McMiner', {prof = 'Miner', labors = {'MINE', 'HAUL_STONE'}, skills = {MINING = 7}, stress = 120000,
        job = {name = 'Dig'}}),
    unit(2, 'Bomrek Brewer', {prof = 'Brewer', labors = {'BREWER', 'COOK'}, skills = {BREWING = 5, COOK = 2},
        stress = -5000}),
}
local others = {
    unit(50, 'Goblin', {invader = true}),
    unit(51, 'Hidden goblin', {invader = true, hidden = true}),
    unit(52, 'Merchant', {merchant = true}),
}
local active = {}
for _, u in ipairs(citizens) do active[#active + 1] = u end
for _, u in ipairs(others) do active[#active + 1] = u end

df = setmetatable({global = {
    cur_year = 105, cur_year_tick = 1200 * 40 + 17, pause_state = false,
    world = {
        cur_savegame = {save_dir = 'region1'},
        items = {other = other},
        buildings = {other = {
            STOCKPILE = {food_pile, drink_pile, wood_pile},
            WORKSHOP_ANY = {carpenter, still, kitchen},
            FURNACE_ANY = {smelter},
            ACTIVITY_ZONE = {
                {id = 200, name = 'DDP Z1 Camere', type = 'Bedroom', x1 = 30, y1 = 40, x2 = 39, y2 = 44, z = 3},
                {id = 201, name = 'Pascolo', type = 'Pen', x1 = 0, y1 = 0, x2 = 1, y2 = 1, z = 5},
            },
        }},
        manager_orders = {all = {
            {id = 7, job_type = 'CustomReaction', reaction_name = 'BREW_DRINK_FROM_PLANT', material_category = {},
             amount_total = 10, amount_left = 4, status = {active = true, validated = true}, frequency = 'OneTime'},
            {id = 8, job_type = 'ConstructBed', reaction_name = '', material_category = {wood = true},
             amount_total = 3, amount_left = 3, status = {active = false, validated = false}, frequency = 'OneTime'},
            {id = 9, job_type = 'MakeCrafts', reaction_name = '', material_category = {},
             amount_total = 5, amount_left = 0, status = {active = false, validated = true}, frequency = 'Daily'},
        }},
        units = {active = active},
    },
}}, {__index = function() return enum() end})

dfhack = {
    df2utf = function(s) return s end,
    getDFHackVersion = function() return '53.17-r1' end,
    getDFVersion = function() return 'v0.53.17 linux64' end,
    translation = {translateName = function(n) return n end},
    world = {getCurrentSite = function() return {id = 42, name = 'Fakefort "Test"'} end},
    matinfo = {decode = function(it) return {getToken = function() return it.mat end} end},
    buildings = {containsTile = function(b, x, y) return not (b.id == 100 and x == 2 and y == 1) end},
    job = {getName = function(j) return j.name end},
    units = {
        getCitizens = function() return citizens end,
        getVisibleName = function(u) return u.name end,
        getProfessionName = function(u) return u.prof end,
        getNominalSkill = function(u, s) return u.skills[s] or 0 end,
        getStressCategory = function(u) return 3 end,
        isActive = function() return true end,
        isHidden = function(u) return u.hidden end,
        isInvader = function(u) return u.invader end,
        isGreatDanger = function(u) return u.great end,
        isDanger = function(u) return u.invader or u.great end,
        isMerchant = function(u) return u.merchant end,
    },
}

-- Map around zone Z1 (x 30..39, y 40..44, z 3): x < 33 dug out, x == 33 designated, rest solid rock.
df.tiletype = {attrs = setmetatable({}, {__index = function(_, tt) return {shape = tt} end})}
df.tiletype_shape = {attrs = setmetatable({}, {__index = function(_, s)
    return {basic_shape = s == 'WALL' and 'Wall' or 'Floor'} end})}
df.tiletype_shape_basic = enum()
df.tile_dig_designation = enum()
df.building_type = enum()
dfhack.maps = {
    getTileType = function(x, y, z) return x < 33 and 'FLOOR' or 'WALL' end,
    getTileFlags = function(x, y, z) return {dig = x == 33 and 'Default' or 'No'} end,
}
local function building(id, t, x, y, z, built)
    return {id = id, x1 = x, y1 = y, x2 = x, y2 = y, z = z, getType = function() return t end,
            getBuildStage = function() return built and 1 or 0 end, getMaxBuildStage = function() return 1 end}
end
df.global.world.buildings.all = {
    building(300, 'Bed', 30, 40, 3, true), building(301, 'Door', 31, 42, 3, false),
    building(302, 'Bed', 30, 40, 4, true), building(100, 'Stockpile', 31, 41, 3, true),
}

-- Burrow "DDP Z2 Laboratori": tiles x 46..49, y 47..49 on z 3, spanning two map blocks, plus one tile on z 2.
local function burrow_block(x, y, z, has)
    return {map_pos = {x = x, y = y, z = z}, has = has}
end
df.global.plotinfo = {burrows = {list = {
    {id = 1, name = 'DDP Z2 Laboratori', blocks = {
        burrow_block(32, 32, 2, function(dx, dy) return dx == 0 and dy == 0 end),
        burrow_block(32, 32, 3, function(dx, dy) return dx >= 14 and dy == 15 end),
        burrow_block(32, 48, 3, function(dx, dy) return dx >= 14 and dy <= 1 end),
        burrow_block(48, 32, 3, function(dx, dy) return dx <= 1 and dy == 15 end),
        burrow_block(48, 48, 3, function(dx, dy) return dx <= 1 and dy <= 1 end),
    }},
    {id = 2, name = 'Rifugio', blocks = {}},
    {id = 3, name = 'DDP Z9 Vuoto', blocks = {}},
}}}
dfhack.burrows = {
    listBlocks = function(b) return b.blocks end,
    isAssignedBlockTile = function(b, block, dx, dy) return block.has(dx, dy) end,
}

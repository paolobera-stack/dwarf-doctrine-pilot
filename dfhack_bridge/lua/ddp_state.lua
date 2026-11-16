-- Dwarf Doctrine Pilot: fortress state exporter (STUB, Step 1).
-- Run with: dfhack-run lua -f ddp_state.lua
-- Must print ONE JSON document matching dfhack_bridge/protocol.py:FortressState.
local json = require('json')

local state = {
    fortress_name = dfhack.TranslateName(df.global.world.world_data.active_site[0].name, true),
    tick = df.global.cur_year_tick,
    paused = df.global.pause_state,
    date = { year = df.global.cur_year, day_of_year = df.global.cur_year_tick // 1200 },
    stocks = {},        -- TODO: count items by ITEM_CATEGORY keys
    ground_items = {},  -- TODO: items not in a stockpile/container
    work_orders = {},   -- TODO: df.global.world.manager_orders
    stockpiles = {},    -- TODO: df.global.world.buildings.other.STOCKPILE
    workshops = {},
    zones = {},         -- TODO: user-designated zones (civzones / burrows named "DDP:*")
    blueprints = {},
    dwarves = {},       -- TODO: dfhack.units.getCitizens()
    events = {},
}

print(json.encode(state))

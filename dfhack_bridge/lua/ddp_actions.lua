-- Dwarf Doctrine Pilot: game actions, run inside DFHack.
-- Usage: dfhack-run lua -f ddp_actions.lua <payload.json> <result.json>
-- payload: {"action": "...", ...}; result: {"ok": true, ...} or {"ok": false, "error": "..."}
local json = require('json')
local utils = require('utils')

local args = {...}
local payload_path, result_path = args[1], args[2]
local W = df.global.world

local function find_order(id)
    for i, o in ipairs(W.manager_orders.all) do
        if o.id == id then return o, i end
    end
end

local function free_order(o)
    for _, c in ipairs(o.item_conditions) do c:delete() end
    for _, c in ipairs(o.order_conditions) do c:delete() end
    if o.items then
        for _, e in ipairs(o.items.elements) do e:delete() end
        o.items:delete()
    end
    o:delete()
end

local actions = {}

-- p.order uses the format of DFHack's order library (`workorder` / `orders import`).
function actions.create_order(p)
    local spec = p.order
    local amount = math.floor(tonumber(p.amount) or 0)
    if amount < 1 then error('amount must be >= 1') end
    local id = W.manager_orders.manager_order_next_id
    reqscript('workorder').create_orders({{
        id = 0, job = spec.job, reaction = spec.reaction, material = spec.material,
        material_category = spec.material_category, meal_ingredients = spec.meal_ingredients,
        frequency = 'OneTime', amount_total = amount,
    }}, true)
    if not find_order(id) then error('order was not created') end
    return {order_id = id}
end

function actions.cancel_order(p)
    local o, idx = find_order(p.order_id)
    if not o then return {missing = true} end
    if p.job_type and df.job_type[o.job_type] ~= p.job_type then
        error(('order %d is a %s, not %s'):format(p.order_id, tostring(df.job_type[o.job_type]), p.job_type))
    end
    W.manager_orders.all:erase(idx)
    free_order(o)
    return {}
end

function actions.set_paused(p)
    dfhack.world.SetPauseState(p.paused and true or false)
    return {paused = df.global.pause_state and true or false}
end

-- Same links DF (and quickfort) create: pile gives to workshop, workshop takes from pile.
function actions.link_stockpile(p)
    local pile = df.building.find(p.stockpile_id)
    local ws = df.building.find(p.workshop_id)
    if not pile or not pile.links then error('stockpile not found: ' .. tostring(p.stockpile_id)) end
    if not ws or not ws.profile then error('workshop not found: ' .. tostring(p.workshop_id)) end
    utils.insert_sorted(pile.links.give_to_workshop, ws, 'id')
    utils.insert_sorted(ws.profile.links.take_from_pile, pile, 'id')
    return {}
end

local result
local ok, err = pcall(function()
    local payload = json.decode_file(payload_path)
    local fn = actions[payload.action] or error('unknown action: ' .. tostring(payload.action))
    result = fn(payload)
end)
if ok then
    result.ok = true
else
    result = {ok = false, error = tostring(err)}
end
json.encode_file(result, result_path)
print('ddp_actions ' .. (ok and 'ok' or 'error'))

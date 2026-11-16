-- Dwarf Doctrine Pilot: action helpers (STUB, Step 1).
-- Usage: dfhack-run lua -f ddp_actions.lua <action> <json-args>
local json = require('json')
local args = {...}
local action, payload = args[1], json.decode(args[2] or '{}')

local actions = {}

function actions.expand_stockpile(p)
    qerror('expand_stockpile not implemented yet')
end

function actions.link_stockpile(p)
    qerror('link_stockpile not implemented yet')
end

local fn = actions[action] or function() qerror('unknown action: ' .. tostring(action)) end
print(json.encode({ ok = true, result = fn(payload) }))

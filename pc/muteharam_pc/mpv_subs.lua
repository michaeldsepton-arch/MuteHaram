-- MuteHaram: subtitles, film-style (white, slightly bold, a thin black outline, centred near the bottom).
--   C  switches  the website's subtitles -> MuteHaram's own (made on the PC with Cohere) -> off
-- MuteHaram's own come from a small JSON file the PC keeps up to date while the film plays (read every 2 s).
-- The website's subtitles (mpv's own) are given the same look, so both always match.
-- Live: tells the PC where playback is (the scene check and the subtitles follow when you skip).
local mp = require "mp"
local utils = require "mp.utils"
local options = require "mp.options"

local o = { subs = "", ["subs-mode"] = "site", ["subs-port"] = "" }
options.read_options(o, "muteharam")
if o.subs == "" then return end

local mode = o["subs-mode"]
local lines = {}
local overlay = mp.create_osd_overlay("ass-events")
overlay.z = -10                              -- under the "Lowering the gaze" cover
local shown = nil

-- the website's subtitles: the same look as ours
mp.set_property("sub-font", "Segoe UI Semibold")
mp.set_property_number("sub-font-size", 46)
mp.set_property_number("sub-border-size", 2.4)
mp.set_property("sub-border-color", "#000000")
mp.set_property("sub-color", "#FFFFFF")
mp.set_property_number("sub-shadow-offset", 0)
mp.set_property_number("sub-margin-y", 40)

local function read_lines()
    local f = io.open(o.subs, "r")
    if not f then return end
    local text = f:read("*a")
    f:close()
    local j = utils.parse_json(text or "")
    if type(j) == "table" and type(j.lines) == "table" then lines = j.lines end
end

local function esc(t)
    return (t or ""):gsub("\\", "\\\\"):gsub("{", "("):gsub("}", ")"):gsub("\n", "\\N")
end

local function current(t)
    -- the lines are sorted by start: a binary search for the last one that started
    local lo, hi, k = 1, #lines, nil
    while lo <= hi do
        local mid = math.floor((lo + hi) / 2)
        if lines[mid][1] <= t then k = mid; lo = mid + 1 else hi = mid - 1 end
    end
    if k and t <= lines[k][2] + 0.4 then return lines[k][3] end
    return nil
end

local function draw()
    if mode ~= "own" then
        if shown then overlay:remove(); shown = nil end
        return
    end
    local t = mp.get_property_number("time-pos")
    local text = t and current(t)
    if text == shown then return end
    shown = text
    if not text then overlay:remove(); return end
    local w, h = mp.get_osd_size()
    if not w or w == 0 then w, h = 1280, 720 end
    overlay.res_x, overlay.res_y = w, h
    local fs = math.floor(h * 0.058)
    overlay.data = string.format("{\\an2\\pos(%d,%d)\\fnSegoe UI Semibold\\fs%d\\1c&HFFFFFF&\\3c&H000000&\\bord%.1f\\shad0\\q2}%s",
        math.floor(w / 2), math.floor(h * 0.94), fs, math.max(1.5, h * 0.0032), esc(text))
    overlay:update()
end

local function apply()
    mp.set_property_bool("sub-visibility", mode == "site")
    shown = false
    draw()
end

local names = { site = "the website's (if it has any)", own = "MuteHaram's own", off = "off" }
local nextmode = { site = "own", own = "off", off = "site" }

mp.add_forced_key_binding("c", "muteharam-subtitles", function()
    mode = nextmode[mode] or "site"
    apply()
    mp.osd_message("Subtitles: " .. names[mode] .. (mode == "own" and #lines == 0 and "  (being made, a moment)" or ""), 3)
    if o["subs-port"] ~= "" then
        mp.command_native_async({ name = "subprocess", playback_only = false, capture_stdout = true,
            args = { "curl", "-s", "-m", "10", string.format("http://127.0.0.1:%s/subs?m=%s", o["subs-port"], mode) } },
            function() end)
    end
end)

-- live: where playback is (the scene check and the subtitles follow a skip)
local last_told = -100
local function tell_pos()
    if o["subs-port"] == "" then return end
    local t = mp.get_property_number("time-pos")
    if not t or math.abs(t - last_told) < 4 then return end
    last_told = t
    mp.command_native_async({ name = "subprocess", playback_only = false, capture_stdout = true,
        args = { "curl", "-s", "-m", "5", string.format("http://127.0.0.1:%s/pos?t=%.1f", o["subs-port"], t) } },
        function() end)
end

read_lines()
apply()
mp.add_periodic_timer(2, read_lines)
mp.add_periodic_timer(0.1, draw)
mp.add_periodic_timer(5, tell_pos)
mp.register_event("seek", function() last_told = -100; tell_pos() end)
mp.observe_property("osd-dimensions", "native", function() shown = false; draw() end)

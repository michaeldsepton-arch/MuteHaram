-- Lowering the gaze (MuteHaram): cover the picture and mute the sound in the parts of the film the scene
-- check found (nudity, something sexual, kissing). The list comes from a small JSON file that the PC keeps up
-- to date (scenes.py); it is read again every 2 seconds, so a live check that is still running works too.
--   G  turns covering on / off (if the check gets it wrong)
-- While the check is still running (live) and playback catches up with it, the film pauses and says so.
local mp = require "mp"
local utils = require "mp.utils"
local options = require "mp.options"

local o = { scenes = "", on = "yes" }
options.read_options(o, "muteharam")
if o.scenes == "" then return end

local on = o.on ~= "no"
local covers, scanned_to, done = {}, 0, false
local covering, held, was_muted = false, false, false
local overlay = mp.create_osd_overlay("ass-events")

local function read_list()
    local f = io.open(o.scenes, "r")
    if not f then return end
    local text = f:read("*a")
    f:close()
    local j = utils.parse_json(text or "")
    if type(j) == "table" then
        covers = j.covers or {}
        scanned_to = tonumber(j.scanned_to) or 0
        done = j.done == true
    end
end

-- the cover: the MuteHaram colours over the whole window, with the words in the middle
local function draw(title, line)
    local w, h = mp.get_osd_size()
    if not w or w == 0 then w, h = 1280, 720 end
    overlay.res_x, overlay.res_y = w, h
    local navy = "{\\1c&H1F1104&\\1a&H00&\\bord0\\shad0}"
    local box = string.format("{\\an7\\pos(0,0)}%s{\\p1}m 0 0 l %d 0 l %d %d l 0 %d{\\p0}", navy, w, w, h, h)
    local s = math.floor(h / 18)
    local ar = string.format("{\\an5\\pos(%d,%d)\\fs%d\\1c&H9AA84F&\\bord0\\shad0}%s", math.floor(w / 2), math.floor(h / 2 - s * 1.6), math.floor(s * 1.1), "غضّ البصر")
    local t = string.format("{\\an5\\pos(%d,%d)\\fs%d\\fnGeorgia\\b1\\1c&HE2ECF2&\\bord0\\shad0}%s", math.floor(w / 2), math.floor(h / 2), math.floor(s * 1.3), title)
    local d = string.format("{\\an5\\pos(%d,%d)\\fs%d\\1c&HC2B8A9&\\bord0\\shad0}%s", math.floor(w / 2), math.floor(h / 2 + s * 1.5), math.floor(s * 0.55), line)
    overlay.data = box .. "\n" .. ar .. "\n" .. t .. "\n" .. d
    overlay:update()
end

local function inside(t)
    for _, c in ipairs(covers) do
        if t >= c[1] and t <= c[2] then return c end
    end
    return nil
end

local function check()
    local t = mp.get_property_number("time-pos")
    if not t then return end
    -- live: never play past what has been checked
    if on and not done and t > scanned_to - 1 then
        if not held then
            held = true
            mp.set_property_bool("pause", true)
            draw("Checking the next scenes …", "It carries on by itself in a moment")
        end
        return
    elseif held and (done or scanned_to > t + 8) then
        held = false
        overlay:remove()
        mp.set_property_bool("pause", false)
    end
    local c = on and inside(t)
    if c and not covering then
        covering = true
        was_muted = mp.get_property_bool("mute")
        mp.set_property_bool("mute", true)
        draw("Lowering the gaze", "A scene is covered · the sound comes back by itself")
    elseif not c and covering then
        covering = false
        mp.set_property_bool("mute", was_muted)
        overlay:remove()
    end
end

read_list()
mp.add_periodic_timer(2, read_list)
mp.add_periodic_timer(0.2, check)
mp.observe_property("osd-dimensions", "native", function()
    if covering then draw("Lowering the gaze", "A scene is covered · the sound comes back by itself") end
end)
mp.add_forced_key_binding("g", "muteharam-gaze", function()
    on = not on
    if not on then
        if covering then covering = false; mp.set_property_bool("mute", was_muted) end
        if held then held = false; mp.set_property_bool("pause", false) end
        overlay:remove()
    end
    mp.osd_message(on and "Lowering the gaze: ON (scenes are covered)" or "Lowering the gaze: OFF", 3)
end)

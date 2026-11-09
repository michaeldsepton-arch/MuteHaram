-- Lowering the gaze (MuteHaram): cover the picture and mute the sound in the parts of the film the scene
-- check found (nudity, something sexual, kissing). The list comes from a small JSON file that the PC keeps up
-- to date (scenes.py); it is read again every 2 seconds, so a live check that is still running works too.
--   G      turns covering on / off (if the check gets it wrong)
--   ENTER  skips to where the covered part ends
-- While covered, the subtitles of what is said are listed on the right (people may still be talking).
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
local cur = nil                 -- the covered part we are in: {start, end}
local said = {}                 -- subtitles while covered (newest last)
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

local function clock(s)
    s = math.floor(s or 0)
    local h, m, sec = math.floor(s / 3600), math.floor(s / 60) % 60, s % 60
    if h > 0 then return string.format("%d:%02d:%02d", h, m, sec) end
    return string.format("%d:%02d", m, sec)
end

local function esc(t)
    return (t or ""):gsub("\\", "\\\\"):gsub("{", "("):gsub("}", ")"):gsub("\n", "\\N")
end

-- the cover: the MuteHaram colours over the whole window, the words in the middle, the subtitles on the right
local function draw(title, line, hint)
    local w, h = mp.get_osd_size()
    if not w or w == 0 then w, h = 1280, 720 end
    overlay.res_x, overlay.res_y = w, h
    local navy = "{\\1c&H1F1104&\\1a&H00&\\bord0\\shad0}"
    local box = string.format("{\\an7\\pos(0,0)}%s{\\p1}m 0 0 l %d 0 l %d %d l 0 %d{\\p0}", navy, w, w, h, h)
    local s = math.floor(h / 18)
    local mid = covering and math.floor(w * 0.36) or math.floor(w / 2)
    local parts = { box }
    parts[#parts + 1] = string.format("{\\an5\\pos(%d,%d)\\fs%d\\1c&H9AA84F&\\bord0\\shad0}%s", mid, math.floor(h / 2 - s * 1.6), math.floor(s * 1.1), "غضّ البصر")
    parts[#parts + 1] = string.format("{\\an5\\pos(%d,%d)\\fs%d\\fnGeorgia\\b1\\1c&HE2ECF2&\\bord0\\shad0}%s", mid, math.floor(h / 2), math.floor(s * 1.3), title)
    parts[#parts + 1] = string.format("{\\an5\\pos(%d,%d)\\fs%d\\1c&HC2B8A9&\\bord0\\shad0}%s", mid, math.floor(h / 2 + s * 1.5), math.floor(s * 0.55), line)
    if hint then
        parts[#parts + 1] = string.format("{\\an5\\pos(%d,%d)\\fs%d\\1c&H9AA84F&\\bord0\\shad0}%s", mid, math.floor(h / 2 + s * 2.6), math.floor(s * 0.6), hint)
    end
    if covering then
        -- the panel on the right: what is being said while the picture is covered
        local x0, y0, x1, y1 = math.floor(w * 0.68), math.floor(h * 0.12), math.floor(w * 0.96), math.floor(h * 0.88)
        parts[#parts + 1] = string.format("{\\an7\\pos(0,0)\\1c&H2B1A08&\\1a&H20&\\bord1\\3c&H9AA84F&\\shad0\\p1}m %d %d l %d %d l %d %d l %d %d{\\p0}", x0, y0, x1, y0, x1, y1, x0, y1)
        local fs = math.floor(s * 0.55)
        parts[#parts + 1] = string.format("{\\an7\\pos(%d,%d)\\fs%d\\b1\\1c&H9AA84F&\\bord0\\shad0}What is said", x0 + fs, y0 + fs, fs)
        local lines = {}
        local first = math.max(1, #said - 9)
        for i = first, #said do lines[#lines + 1] = esc(said[i]) end
        local body = #lines > 0 and table.concat(lines, "\\N\\N") or "(no subtitles: turn them on with J)"
        parts[#parts + 1] = string.format("{\\an7\\pos(%d,%d)\\fs%d\\1c&HE2ECF2&\\bord0\\shad0\\q0}%s", x0 + fs, y0 + fs * 3, fs, body)
    end
    overlay.data = table.concat(parts, "\n")
    overlay:update()
end

local function cover_text()
    draw("Lowering the gaze", "A scene is covered · the sound comes back by itself",
         cur and ("ENTER: skip to " .. clock(cur[2]) .. "  ▶") or nil)
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
        cur = c
        said = {}
        was_muted = mp.get_property_bool("mute")
        mp.set_property_bool("mute", true)
        cover_text()
    elseif c and covering and (c[2] ~= (cur and cur[2])) then
        cur = c                                 -- a live check made it longer
        cover_text()
    elseif not c and covering then
        covering = false
        cur = nil
        mp.set_property_bool("mute", was_muted)
        overlay:remove()
    end
end

read_list()
mp.add_periodic_timer(2, read_list)
mp.add_periodic_timer(0.2, check)
mp.observe_property("osd-dimensions", "native", function()
    if covering then cover_text() end
end)
mp.observe_property("sub-text", "string", function(_, text)
    if covering and text and text ~= "" and said[#said] ~= text then
        said[#said + 1] = text
        cover_text()
    end
end)
mp.add_forced_key_binding("ENTER", "muteharam-gaze-skip", function()
    if covering and cur then
        mp.commandv("seek", tostring(cur[2] + 0.2), "absolute")
    end
end)
mp.add_forced_key_binding("g", "muteharam-gaze", function()
    on = not on
    if not on then
        if covering then covering = false; cur = nil; mp.set_property_bool("mute", was_muted) end
        if held then held = false; mp.set_property_bool("pause", false) end
        overlay:remove()
    end
    mp.osd_message(on and "Lowering the gaze: ON (scenes are covered)" or "Lowering the gaze: OFF", 3)
end)

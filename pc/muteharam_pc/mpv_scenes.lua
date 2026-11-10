-- Lowering the gaze (MuteHaram): cover the picture and mute the sound in the parts of the film the scene
-- check found (nudity, something sexual, kissing). The list comes from a small JSON file that the PC keeps up
-- to date (scenes.py); it is read again every 2 seconds, so a live check that is still running works too.
--   G      turns covering on / off (if the check gets it wrong)
--   ENTER  skips to where the covered part ends
-- While covered, what is said is shown in a wide box in the middle (people may still be talking): MuteHaram's own
-- subtitles (Cohere), else the website's.
-- Live: the film waits at the start until the check is far enough ahead that it never has to stop for it later
-- (how far depends on how fast the check goes); if playback still catches up with it, it pauses and says so.
local mp = require "mp"
local utils = require "mp.utils"
local options = require "mp.options"

local o = { scenes = "", on = "yes", subs = "", logo = "" }
options.read_options(o, "muteharam")
if o.scenes == "" then return end

local on = o.on ~= "no"
local covers, scanned_to, done = {}, 0, false
local scanned, need, rate = nil, 45, nil     -- the checked parts; how far ahead it must be; its speed
local covering, held, was_muted = false, false, false
local started = false            -- the film has started playing (the first wait is "Getting ready")
local cur = nil                 -- the covered part we are in: {start, end}
local said = {}                 -- the website's subtitles while covered (newest last), if it has any
local made = {}                 -- MuteHaram's own subtitles of the covered parts: {start, end, text}
local shown = -1                -- how many of them are on the panel now
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
        scanned = j.scanned
        need = tonumber(j.need) or 45
        rate = tonumber(j.rate)
        if o.subs == "" then made = j.said or {} end
    end
    if o.subs ~= "" then                          -- MuteHaram's own subtitles (the whole film, or the covered parts)
        local g = io.open(o.subs, "r")
        if g then
            local sj = utils.parse_json(g:read("*a") or "")
            g:close()
            if type(sj) == "table" and type(sj.lines) == "table" then made = sj.lines end
        end
    end
end

-- how far the checked part reaches from t on
local function ahead(t)
    if type(scanned) == "table" then
        for _, r in ipairs(scanned) do
            if t >= r[1] - 0.5 and t <= r[2] then return r[2] end
        end
        return t
    end
    return scanned_to
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

-- the cover: the MuteHaram colours over the whole window; the words near the top; what is said in a wide box in
-- the middle under them (newest line brightest, the two before it dimmer); the skip hint at the bottom
local function rect(x0, y0, x1, y1, r)
    -- a rounded rectangle as an ASS drawing (corner curves with the usual 0.55 control points)
    local k = math.floor(r * 0.45)
    return string.format("m %d %d l %d %d b %d %d %d %d %d %d l %d %d b %d %d %d %d %d %d l %d %d b %d %d %d %d %d %d l %d %d b %d %d %d %d %d %d",
        x0 + r, y0, x1 - r, y0, x1 - k, y0, x1, y0 + k, x1, y0 + r, x1, y1 - r, x1, y1 - k, x1 - k, y1, x1 - r, y1,
        x0 + r, y1, x0 + k, y1, x0, y1 - k, x0, y1 - r, x0, y0 + r, x0, y0 + k, x0 + k, y0, x0 + r, y0)
end

-- the logo (small raw pictures the PC made next to the scene list: <logo>-<size>.bgra)
local logo_shown = false
local function show_logo(x, y, size)
    if o.logo == "" then return end
    local best = nil
    for _, n in ipairs({ 48, 64, 80, 100, 128, 160 }) do
        if not best or math.abs(n - size) < math.abs(best - size) then best = n end
    end
    mp.commandv("overlay-add", "7", tostring(math.floor(x - best / 2)), tostring(math.floor(y)),
        o.logo .. "-" .. best .. ".bgra", "0", "bgra", tostring(best), tostring(best), tostring(best * 4))
    logo_shown = true
end
local function hide_logo()
    if logo_shown then mp.commandv("overlay-remove", "7"); logo_shown = false end
end

-- the background: navy to teal, corner to corner (as on the tablet), drawn as diagonal bands
local function backdrop(w, h)
    local stops = { { 0.0, 0x04, 0x0E, 0x19 }, { 0.55, 0x0A, 0x1F, 0x33 }, { 1.0, 0x15, 0x52, 0x4F } }
    local function colour(f)
        for i = 1, #stops - 1 do
            local a, b = stops[i], stops[i + 1]
            if f <= b[1] then
                local k = (f - a[1]) / (b[1] - a[1])
                local r = math.floor(a[2] + (b[2] - a[2]) * k + 0.5)
                local g = math.floor(a[3] + (b[3] - a[3]) * k + 0.5)
                local bl = math.floor(a[4] + (b[4] - a[4]) * k + 0.5)
                return string.format("&H%02X%02X%02X&", bl, g, r)
            end
        end
        return "&H4F5215&"
    end
    local out = {}
    local n = 48
    local span = w + h
    for k = 0, n - 1 do
        local d0, d1 = span * k / n, span * (k + 1) / n + 2
        -- the band where d0 <= x + y <= d1
        out[#out + 1] = string.format("{\\an7\\pos(0,0)\\1c%s\\bord0\\shad0\\p1}m %d 0 l %d 0 l %d %d l %d %d{\\p0}",
            colour((k + 0.5) / n), math.floor(d0), math.floor(d1), math.floor(d1 - h), h, math.floor(d0 - h), h)
    end
    return table.concat(out, "\n")
end

local skip_box = nil            -- where the Skip button is (it can be clicked)
local function utf8len(t)
    local _, n = t:gsub("[^\128-\191]", "")
    return n
end

local clicking = false
local function skip_click()
    local m = mp.get_property_native("mouse-pos")
    if skip_box and cur and m and m.x >= skip_box[1] and m.x <= skip_box[3] and m.y >= skip_box[2] and m.y <= skip_box[4] then
        mp.commandv("seek", tostring(cur[2] + 0.2), "absolute")
    end
end

-- sizes and spacing follow the mock-up exactly (measured from it): u = 1 % of the picture's width (16:10 or wider).
-- A CSS font size s becomes an ASS size s * 1.32 for Segoe UI (then one line is as tall as in the mock-up), and
-- s * 1.14 for Georgia.
local SEG, GEO = 1.32, 1.14
local function draw(title, line, hint, sub)
    local w, h = mp.get_osd_size()
    if not w or w == 0 then w, h = 1280, 720 end
    overlay.res_x, overlay.res_y = w, h
    local u = math.min(w, h * 1.6) / 100
    local mid = math.floor(w / 2)
    local parts = { backdrop(w, h) }
    local function text(y, css, k, style, s)
        parts[#parts + 1] = string.format("{\\an8\\pos(%d,%d)\\fs%d\\bord0\\shad0%s}%s", mid, math.floor(y), math.floor(css * k + 0.5), style, s)
    end
    local y = covering and 4.5 * u or h / 2 - 15 * u
    local logo = math.floor(5.2 * u)
    show_logo(mid, y, logo)
    y = y + logo + 0.8 * u
    text(y, 2.6 * u, SEG, "\\fnSegoe UI\\1c&H9AA84F&", "غضّ البصر")
    y = y + 2.6 * u * SEG
    text(y, 3.4 * u, GEO, "\\fnGeorgia\\b1\\1c&HE2ECF2&", title)
    y = y + 3.4 * u * GEO + 0.5 * u
    text(y, 1.45 * u, SEG, "\\fnSegoe UI\\1c&HC2B8A9&", line)
    y = y + 1.45 * u * SEG
    if sub then
        y = y + 1.4 * u
        text(y, 1.9 * u, SEG, "\\fnSegoe UI\\1c&H9AA84F&", sub)
        y = y + 1.9 * u * SEG
    end
    skip_box = nil
    if hint then
        -- the Skip button: a teal pill at the bottom (click it, or press Enter)
        local css = 1.75 * u
        local tw = utf8len(hint) * 0.43 * css           -- about how wide the words are (bold Segoe UI, measured)
        local bw, bh = math.floor(tw + 4.8 * u), math.floor(css * SEG + 2 * u)
        local by1 = math.floor(h - 4 * u)
        local bx0, by0 = mid - math.floor(bw / 2), by1 - bh
        skip_box = { bx0, by0, bx0 + bw, by1 }
        if not clicking then mp.add_forced_key_binding("MBTN_LEFT", "muteharam-skip-click", skip_click); clicking = true end
        parts[#parts + 1] = string.format("{\\an7\\pos(0,0)\\1c&H4F5215&\\1a&H26&\\bord1\\3c&H9AA84F&\\shad0\\p1}%s{\\p0}",
            rect(bx0, by0, bx0 + bw, by1, math.floor(bh / 2)))
        text(by0 + u, css, SEG, "\\fnSegoe UI\\b1\\1c&HE2ECF2&", hint)
        text(by1 + 0.5 * u, 1.05 * u, SEG, "\\fnSegoe UI\\1c&HC2B8A9&", "or press Enter")
    end
    if covering then
        -- what is being said while the picture is covered: a box 80 % of the picture wide, as tall as its words
        local lines = {}
        local t = mp.get_property_number("time-pos") or 0
        for _, l in ipairs(made) do
            if cur and l[1] >= cur[1] - 1 and l[1] <= t + 0.3 then lines[#lines + 1] = l[3] end
        end
        shown = #lines
        if #lines == 0 then
            for i = math.max(1, #said - 2), #said do lines[#lines + 1] = said[i] end
        end
        while #lines > 3 do table.remove(lines, 1) end
        local x0, x1 = math.floor(w / 2 - 40 * u), math.floor(w / 2 + 40 * u)
        local y0 = y + 2.6 * u
        local box_at = #parts + 1
        parts[box_at] = ""                          -- (the box, drawn under the words once its height is known)
        local cy = y0 + 1.8 * u
        text(cy, 1.25 * u, SEG, string.format("\\fnSegoe UI\\b1\\fsp%d\\1c&H9AA84F&", math.floor(0.17 * u + 0.5)), "WHAT IS SAID")
        cy = cy + 1.25 * u * SEG
        if #lines == 0 then
            cy = cy + 1.1 * u
            text(cy, 2.15 * u, SEG, "\\fnSegoe UI\\1c&HC2B8A9&", "(nothing said yet)")
            cy = cy + 2.15 * u * SEG
        else
            for i, l in ipairs(lines) do
                local newest = i == #lines
                local css = (newest and 2.85 or 2.15) * u
                local _, breaks = l:gsub("\n", "")
                cy = cy + 1.1 * u
                text(cy, css, SEG, newest and "\\fnSegoe UI Semibold\\1c&HE2ECF2&\\q0" or "\\fnSegoe UI\\1c&HE2ECF2&\\1a&H80&\\q0", esc(l))
                cy = cy + css * SEG * (breaks + 1)
            end
        end
        local y1 = cy + 2.2 * u
        parts[box_at] = string.format("{\\an7\\pos(0,0)\\1c&H190E04&\\1a&H62&\\bord1\\3c&H9AA84F&\\3a&H99&\\shad0\\p1}%s{\\p0}",
            rect(x0, math.floor(y0), x1, math.floor(y1), math.floor(1.6 * u)))
    end
    overlay.data = table.concat(parts, "\n")
    overlay:update()
end

local function undraw()
    overlay:remove()
    hide_logo()
    skip_box = nil
    if clicking then mp.remove_key_binding("muteharam-skip-click"); clicking = false end
end

local function cover_text()
    draw("Lowering the gaze", "A scene is covered · the sound comes back by itself", cur and
         ("Skip  ▶   (" .. clock(math.max(0, cur[2] - (mp.get_property_number("time-pos") or 0))) .. ")") or nil)
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
    -- (playback stays well behind the check: a scene is found a few seconds in and covered from before it);
    -- at the start, and after a hold, it waits until the check is as far ahead as it needs to stay ahead
    local reach = ahead(t)
    local want = math.max(40, need)
    if on and not done and (t > reach - 15 or (not started and reach - t < want)) then
        if not held then
            held = true
            mp.set_property_bool("pause", true)
        end
        local pct = math.max(0, math.min(99, math.floor((reach - t) / want * 100)))
        local left = rate and rate > 0 and math.max(1, math.floor((want - (reach - t)) / rate)) or nil
        draw(started and "Checking the next scenes …" or "Getting ready …",
             "Checking the scenes ahead, so the film does not have to stop for it later",
             nil, string.format("%d %%%s", pct, left and string.format("   ·   about %s left", clock(left)) or ""))
        return
    elseif held and (done or reach - t >= want) then
        held = false
        undraw()
        mp.set_property_bool("pause", false)
    end
    started = true
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
    elseif c and covering and cur then
        -- a new line of MuteHaram's subtitles has just been spoken: show it
        local n = 0
        for _, l in ipairs(made) do
            if l[1] >= cur[1] - 1 and l[1] <= t + 0.3 then n = n + 1 end
        end
        if n ~= shown then cover_text() end
    elseif not c and covering then
        covering = false
        cur = nil
        mp.set_property_bool("mute", was_muted)
        undraw()
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
        undraw()
    end
    mp.osd_message(on and "Lowering the gaze: ON (scenes are covered)" or "Lowering the gaze: OFF", 3)
end)

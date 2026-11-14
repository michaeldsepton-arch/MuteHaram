-- MuteHaram: how strict the music removal is, while watching (live cleaning).
--   L  cycles  Auto -> Normal -> Strong -> Strongest (speech only) -> Auto
-- Normal is the default (it sounds best). Auto makes each part as strict as it needs: where music is still heard after the normal
-- cleaning, that part is cleaned again more strictly (ahead of playback, nothing pauses).
-- Choosing a level by hand cleans again from where you are: the film pauses for a few seconds until it is ready.
local mp = require "mp"
local port = mp.get_opt("muteharam-level-port")
local level = mp.get_opt("muteharam-level") or "normal"
if not port then return end

local names = {
    auto      = "Auto (as strict as each part needs)",
    normal    = "Normal (music out; voices and effects stay)",
    strong    = "Strong (a second model takes the music out)",
    strongest = "Strongest (speech only)",
}
local nextlevel = { auto = "normal", normal = "strong", strong = "strongest", strongest = "auto" }
local busy = false

mp.add_forced_key_binding("l", "muteharam-strength", function()
    if busy then
        mp.osd_message("MuteHaram: still changing the strength, it carries on by itself when ready", 3)
        return
    end
    busy = true
    local want = nextlevel[level] or "auto"
    local was_paused = mp.get_property_bool("pause")
    local pos = mp.get_property_number("time-pos") or 0
    mp.set_property_bool("pause", true)
    local started = mp.get_time()
    local ticker = mp.add_periodic_timer(1, function()
        mp.osd_message(string.format("Music removal: %s\nCleaning again from here ... %d s", names[want],
            math.floor(mp.get_time() - started)), 2)
    end)
    mp.osd_message("Music removal: " .. names[want] .. "\nCleaning again from here ...", 2)
    mp.command_native_async({
        name = "subprocess", playback_only = false, capture_stdout = true,
        args = { "curl", "-s", "-m", "180", string.format("http://127.0.0.1:%s/level?l=%s&pos=%d", port, want, math.floor(pos * 1000)) },
    }, function(ok, res, err)
        ticker:kill()
        busy = false
        if ok and res and res.status == 0 and (res.stdout or ""):find('"ok": true', 1, true) then
            level = want
            mp.commandv("audio-reload")          -- the sound already loaded ahead was the old strength
            mp.add_timeout(0.6, function()
                mp.commandv("seek", tostring(pos), "absolute+exact")
                mp.set_property_bool("pause", was_paused)
            end)
            mp.osd_message("Music removal: " .. names[level], 5)
        else
            mp.set_property_bool("pause", was_paused)
            mp.osd_message("MuteHaram: could not change the strength (" .. tostring(err or (res and res.stdout) or "no answer")
                .. ")", 6)
        end
    end)
end)

mp.add_timeout(2, function() mp.osd_message("Music removal: " .. names[level] .. "   (L to change · H if you hear music)", 4) end)

-- H: "music here" - music slipped through just now. The PC notes this moment (numbers only, no sound is kept) so
-- the cleaning can be improved later.
mp.add_forced_key_binding("h", "muteharam-music-here", function()
    local pos = mp.get_property_number("time-pos") or 0
    mp.command_native_async({
        name = "subprocess", playback_only = false, capture_stdout = true,
        args = { "curl", "-s", "-m", "10", string.format("http://127.0.0.1:%s/music_here?pos=%d", port, math.floor(pos * 1000)) },
    }, function(ok, res)
        if ok and res and res.status == 0 and (res.stdout or ""):find('"ok": true', 1, true) then
            mp.osd_message("♪ Music here: noted, thank you (it helps improve the cleaning)", 3)
        else
            mp.osd_message("MuteHaram: could not note it", 3)
        end
    end)
end)

-- MuteHaram: switch the cleaning between "remove the music" and "speech only" while watching.
-- Key: C  (also a button in the controls bar). The PC re-cleans from where you are; a few seconds of wait.
local port = mp.get_opt("muteharam-mode-port")
local mode = mp.get_opt("muteharam-mode") or "music"
if not port then return end

local names = {
    music  = "REMOVE THE MUSIC (voices and sound effects stay)",
    speech = "SPEECH ONLY (music and sound effects removed)",
}
local busy = false

local function toggle()
    if busy then return end
    busy = true
    local want = (mode == "music") and "speech" or "music"
    mp.osd_message("MuteHaram: switching to " .. names[want] .. " ...", 6)
    mp.command_native_async({
        name = "subprocess", playback_only = false, capture_stdout = true,
        args = { "curl", "-s", "-m", "30", "http://127.0.0.1:" .. port .. "/mode?m=" .. want },
    }, function(ok, res, err)
        busy = false
        if ok and res and res.status == 0 and (res.stdout or ""):find(want, 1, true) then
            mode = want
            mp.commandv("audio-reload")
            mp.add_timeout(0.6, function() mp.commandv("seek", "0", "relative+exact") end)
            mp.osd_message("MuteHaram cleaning: " .. names[mode], 6)
        else
            mp.osd_message("MuteHaram: could not switch the cleaning (" .. tostring(err or (res and res.status) or "no answer") .. ")", 6)
        end
    end)
end

mp.add_key_binding("c", "muteharam-mode", toggle)

mp.register_event("file-loaded", function()
    mp.osd_message("MuteHaram cleaning: " .. names[mode] .. "\nPress C to switch", 6)
end)

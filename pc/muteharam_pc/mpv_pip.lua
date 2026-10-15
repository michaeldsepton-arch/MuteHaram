-- MuteHaram: picture-in-picture style window for mpv.
-- Press  P  (or Shift+P) to toggle: small, frameless, always-on-top window in the
-- bottom-right corner of the screen. Press it again to go back to a normal window.
-- (mpv's built-in key  T  also toggles "always on top" by itself.)

local pip_on = false
local saved = {}

local function enter_pip()
    saved.ontop = mp.get_property_native("ontop")
    saved.border = mp.get_property_native("border")
    saved.fs = mp.get_property_native("fullscreen")
    saved.scale = mp.get_property_number("window-scale", 1)
    if saved.fs then mp.set_property_native("fullscreen", false) end
    mp.set_property_native("ontop", true)
    mp.set_property_native("border", false)
    -- about a quarter of the screen width, whatever the video size
    local dw = mp.get_property_number("display-width")
    local w = mp.get_property_number("width") or 1280
    if dw and w > 0 then
        mp.set_property_number("window-scale", (dw * 0.25) / w)
    else
        mp.set_property_number("window-scale", 0.3)
    end
    -- best effort: bottom-right corner (not every mpv build moves an open window)
    mp.set_property("geometry", "98%:94%")
    mp.osd_message("MuteHaram: small window (press P to go back, drag to move)", 3)
    pip_on = true
end

local function leave_pip()
    mp.set_property_native("ontop", saved.ontop or false)
    mp.set_property_native("border", saved.border == nil and true or saved.border)
    mp.set_property_number("window-scale", saved.scale or 1)
    pip_on = false
end

local function toggle()
    if pip_on then leave_pip() else enter_pip() end
end

mp.add_key_binding("p", "muteharam-pip", toggle)
mp.add_key_binding("P", "muteharam-pip-shift", toggle)

-- started with --script-opts=muteharam-pip=yes -> begin in PiP mode
mp.register_event("file-loaded", function()
    if mp.get_opt("muteharam-pip") == "yes" and not pip_on then
        enter_pip()
    end
end)

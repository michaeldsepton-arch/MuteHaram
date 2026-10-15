-- MuteHaram: when a video has its own audio tracks AND our cleaned external
-- track, play the cleaned one.
mp.register_event("file-loaded", function()
    local tracks = mp.get_property_native("track-list") or {}
    local chosen = nil
    for _, t in ipairs(tracks) do
        if t.type == "audio" and t.external then
            chosen = t.id
        end
    end
    if chosen then
        mp.set_property_number("aid", chosen)
        mp.msg.info("MuteHaram: using cleaned audio track " .. chosen)
    end
end)

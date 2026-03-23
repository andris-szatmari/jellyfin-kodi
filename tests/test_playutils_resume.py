from jellyfin_kodi.helper import playutils as playutils_module


def test_set_properties_carries_resume_startup_intent(monkeypatch):
    recorded = {}

    def fake_window(key, value=None, clear=False):
        if value is not None:
            recorded[key] = value
            return value

        return recorded.get(key)

    item = {
        "Type": "Episode",
        "Id": "item-id",
        "ResumeIntent": playutils_module.RESUME_INTENT_STARTOVER,
        "RequestedStartOffset": 0,
        "ForceInitialSeek": True,
        "PromptSource": playutils_module.RESUME_PROMPT_SOURCE_NATIVE,
        "PlaybackInfo": {
            "Path": "https://example.test/stream",
            "PlaySessionId": "play-session-id",
            "MediaSourceId": "media-source-id",
            "CurrentPosition": 0,
            "ResumeIntent": playutils_module.RESUME_INTENT_STARTOVER,
            "RequestedStartOffset": 0,
            "ForceInitialSeek": True,
            "PromptSource": playutils_module.RESUME_PROMPT_SOURCE_NATIVE,
        },
    }

    monkeypatch.setattr(playutils_module, "window", fake_window)
    monkeypatch.setattr(playutils_module.client, "get_device_id", lambda: "device-id")

    playutils_module.set_properties(item, "DirectStream", "server-id")

    payload = recorded["jellyfin_play.json"][0]

    assert payload["CurrentPosition"] == 0
    assert payload["ResumeIntent"] == playutils_module.RESUME_INTENT_STARTOVER
    assert payload["RequestedStartOffset"] == 0
    assert payload["ForceInitialSeek"] is True
    assert payload["PromptSource"] == playutils_module.RESUME_PROMPT_SOURCE_NATIVE

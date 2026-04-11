from types import SimpleNamespace

from jellyfin_kodi.entrypoint import service as service_module
from jellyfin_kodi import player as player_module


def make_sleep_resume_item():
    return {
        "Type": "Episode",
        "Id": "item-id",
        "Path": "/media/episode.mkv",
        "File": "/media/episode.mkv",
        "PlayMethod": "DirectStream",
        "PlayOption": "Addon",
        "MediaSourceId": "media-source-id",
        "Runtime": 1800,
        "PlaySessionId": "play-session-id",
        "ServerId": "server-id",
        "DeviceId": "device-id",
        "SubsMapping": {},
        "AudioStreamIndex": 1,
        "SubtitleStreamIndex": 2,
        "CurrentEpisode": {"episodeid": "item-id"},
        "CurrentPosition": 803,
        "Muted": False,
        "Volume": 97,
    }


def patch_window_state(monkeypatch, state):
    def fake_window(key, value=None, clear=False):
        if clear:
            state.pop(key, None)
            return None

        if value is not None:
            state[key] = value
            return None

        return state.get(key)

    monkeypatch.setattr(player_module, "window", fake_window)


# Sleep teardown should persist the currently playing Jellyfin item with an
# explicit resume target so the wake-start path can recover it later.
def test_store_sleep_resume_candidate_persists_active_playback(monkeypatch):
    player = player_module.Player()
    item = make_sleep_resume_item()
    state = {}

    player.played = {item["File"]: item}

    patch_window_state(monkeypatch, state)
    monkeypatch.setattr(player, "get_playing_file", lambda: item["File"])
    monkeypatch.setattr(player, "getTime", lambda: 815)

    candidate = player.store_sleep_resume_candidate()

    assert candidate["Id"] == "item-id"
    assert candidate["RequestedStartOffset"] == 815
    assert candidate["ResumeIntent"] == player_module.playutils.RESUME_INTENT_RESUME
    assert candidate["ForceInitialSeek"] is True
    assert state[player_module.Player.SLEEP_RESUME_ACTIVE_KEY] is True
    assert state[player_module.Player.SLEEP_RESUME_STATE_KEY]["CurrentPosition"] == 815


# When Shield/Kodi restarts playback after wake without jellyfin_play.json,
# the player should recover the stored sleep candidate immediately rather than
# waiting for the normal addon startup path that never comes.
def test_on_playback_started_recovers_sleep_resume_without_play_window(monkeypatch):
    player = player_module.Player()
    item = make_sleep_resume_item()
    state = {
        player_module.Player.SLEEP_RESUME_ACTIVE_KEY: True,
        player_module.Player.SLEEP_RESUME_STATE_KEY: {
            **item,
            "CurrentPosition": 815,
            "RequestedStartOffset": 815,
            "ResumeIntent": player_module.playutils.RESUME_INTENT_RESUME,
            "ForceInitialSeek": True,
            "PromptSource": player_module.Player.SLEEP_RESUME_PROMPT_SOURCE,
            "Paused": False,
        },
    }
    calls = []

    class AbortAfterStartupMonitor:
        def waitForAbort(self, timeout):
            calls.append("wait:%s" % timeout)
            return True

    def fake_set_item(current_file, current_item):
        calls.append(("set_item", current_file, current_item["RequestedStartOffset"]))
        current_item["Server"] = SimpleNamespace(
            jellyfin=SimpleNamespace(
                session_playing=lambda data: calls.append(
                    ("session", data["PositionTicks"])
                )
            )
        )

    patch_window_state(monkeypatch, state)
    monkeypatch.setattr(player, "stop_playback", lambda: calls.append("stop"))
    monkeypatch.setattr(player, "getPlayingFile", lambda: item["File"])
    monkeypatch.setattr(player, "set_item", fake_set_item)
    monkeypatch.setattr(
        player,
        "schedule_initial_seek",
        lambda current_file: calls.append(("schedule", current_file)),
    )
    monkeypatch.setattr(
        player,
        "set_audio_subs",
        lambda audio, subtitle: calls.append(("audio_subs", audio, subtitle)),
    )
    monkeypatch.setattr(player_module, "settings", lambda key: False)
    monkeypatch.setattr(
        player_module.xbmc,
        "Monitor",
        lambda: AbortAfterStartupMonitor(),
    )

    player.onPlayBackStarted()

    assert calls == [
        "stop",
        ("set_item", "/media/episode.mkv", 815),
        ("session", 8150000000),
        ("schedule", "/media/episode.mkv"),
        "wait:2",
    ]
    assert player_module.Player.SLEEP_RESUME_ACTIVE_KEY not in state
    assert player_module.Player.SLEEP_RESUME_STATE_KEY not in state


# The service sleep hook should store the active playback candidate before it
# closes Jellyfin clients, otherwise the wake-start path has nothing to recover.
def test_service_on_sleep_stores_candidate_before_teardown(monkeypatch):
    calls = []
    service = service_module.Service.__new__(service_module.Service)
    service.monitor = SimpleNamespace(
        player=SimpleNamespace(
            store_sleep_resume_candidate=lambda: calls.append("store_candidate")
        ),
        server=["default"],
        sleep=False,
    )
    service.library_thread = None

    monkeypatch.setattr(service_module, "window", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service_module.Jellyfin, "close_all", lambda: calls.append("close_all")
    )

    service_module.Service.onNotification(service, "xbmc", "System.OnSleep", "{}")

    assert calls == ["store_candidate", "close_all"]
    assert service.monitor.server == []
    assert service.monitor.sleep is True

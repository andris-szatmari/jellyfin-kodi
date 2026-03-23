from types import SimpleNamespace

from jellyfin_kodi import player as player_module


class PassiveMonitor:
    @staticmethod
    def waitForAbort(timeout):
        return False


def make_player_item(intent, requested_start_offset, force_initial_seek=True):
    return {
        "Id": "item-id",
        "File": "/media/episode.mkv",
        "CurrentPosition": requested_start_offset,
        "RequestedStartOffset": requested_start_offset,
        "ResumeIntent": intent,
        "ForceInitialSeek": force_initial_seek,
        "Runtime": 1800,
        "MediaSourceId": "media-source-id",
        "PlaySessionId": "play-session-id",
        "PlayMethod": "DirectStream",
        "Server": SimpleNamespace(jellyfin=SimpleNamespace()),
    }


def test_apply_initial_seek_seeks_to_resume_offset_when_needed(monkeypatch):
    player = player_module.Player()
    item = make_player_item(player_module.playutils.RESUME_INTENT_RESUME, 948.0)
    player.played = {item["File"]: item}
    times = iter([0, 948])
    seek_calls = []

    monkeypatch.setattr(player, "get_playing_file", lambda: item["File"])
    monkeypatch.setattr(player, "getTime", lambda: next(times))
    monkeypatch.setattr(
        player,
        "_jsonrpc_seek",
        lambda target: (seek_calls.append(target), {"percentage": 52.7})[1],
    )

    player._apply_initial_seek(item["File"], generation=0, monitor=PassiveMonitor())

    assert seek_calls == [948.0]
    assert item["CurrentPosition"] == 948
    assert item["ForceInitialSeek"] is False


def test_apply_initial_seek_skips_seek_to_zero_when_near_start(monkeypatch):
    player = player_module.Player()
    item = make_player_item(player_module.playutils.RESUME_INTENT_STARTOVER, 0)
    player.played = {item["File"]: item}
    seek_calls = []

    monkeypatch.setattr(player, "get_playing_file", lambda: item["File"])
    monkeypatch.setattr(player, "getTime", lambda: 8)
    monkeypatch.setattr(
        player,
        "_jsonrpc_seek",
        lambda target: (seek_calls.append(target), {"percentage": 0})[1],
    )

    player._apply_initial_seek(item["File"], generation=0, monitor=PassiveMonitor())

    assert seek_calls == []
    assert item["CurrentPosition"] == 0
    assert item["ForceInitialSeek"] is False


def test_apply_initial_seek_does_seek_to_zero_when_far_from_start(monkeypatch):
    player = player_module.Player()
    item = make_player_item(player_module.playutils.RESUME_INTENT_STARTOVER, 0)
    player.played = {item["File"]: item}
    times = iter([120, 0])
    seek_calls = []

    monkeypatch.setattr(player, "get_playing_file", lambda: item["File"])
    monkeypatch.setattr(player, "getTime", lambda: next(times))
    monkeypatch.setattr(
        player,
        "_jsonrpc_seek",
        lambda target: (seek_calls.append(target), {"percentage": 0})[1],
    )

    player._apply_initial_seek(item["File"], generation=0, monitor=PassiveMonitor())

    assert seek_calls == [0]
    assert item["CurrentPosition"] == 0
    assert item["ForceInitialSeek"] is False


def test_apply_initial_seek_aborts_when_playing_file_changes(monkeypatch):
    player = player_module.Player()
    item = make_player_item(player_module.playutils.RESUME_INTENT_RESUME, 948.0)
    player.played = {item["File"]: item}

    monkeypatch.setattr(player, "get_playing_file", lambda: "/media/other-episode.mkv")

    player._apply_initial_seek(item["File"], generation=0, monitor=PassiveMonitor())

    assert item["ForceInitialSeek"] is True


def test_schedule_initial_seek_ignores_items_without_explicit_intent(monkeypatch):
    player = player_module.Player()
    item = make_player_item(player_module.playutils.RESUME_INTENT_NONE, None, False)
    player.played = {item["File"]: item}
    spawned = {}

    class RecordingThread:
        def __init__(self, target, args, name):
            spawned["target"] = target
            spawned["args"] = args
            spawned["name"] = name
            self.daemon = False

        def start(self):
            spawned["started"] = True

    monkeypatch.setattr(player_module.threading, "Thread", RecordingThread)

    player.schedule_initial_seek(item["File"])

    assert spawned == {}


def test_set_item_preserves_explicit_zero_start_offset(monkeypatch):
    player = player_module.Player()
    item = {
        "Id": "item-id",
        "Runtime": 1800,
        "ServerId": "server-id",
        "CurrentPosition": 0,
    }

    monkeypatch.setattr(player, "getTime", lambda: 47)
    monkeypatch.setattr(
        player_module,
        "JSONRPC",
        lambda method: SimpleNamespace(
            execute=lambda params: {"result": {"volume": 100, "muted": False}}
        ),
    )
    monkeypatch.setattr(
        player_module,
        "Jellyfin",
        lambda server_id: SimpleNamespace(get_client=lambda: "server-client"),
    )

    player.set_item("/media/episode.mkv", item)

    assert item["CurrentPosition"] == 0


def test_on_playback_started_schedules_initial_seek_before_startup_wait(monkeypatch):
    player = player_module.Player()
    calls = []
    file_path = "/media/episode.mkv"
    item = {
        "Id": "item-id",
        "Path": file_path,
        "MediaSourceId": "media-source-id",
        "PlayMethod": "DirectStream",
        "Volume": 93,
        "CurrentPosition": 948.0,
        "Paused": False,
        "Muted": False,
        "PlaySessionId": "play-session-id",
        "AudioStreamIndex": 1,
        "SubtitleStreamIndex": None,
        "PlayOption": "Addon",
        "Server": SimpleNamespace(
            jellyfin=SimpleNamespace(session_playing=lambda data: calls.append("session"))
        ),
    }
    state = {"jellyfin_play.json": [item]}

    class AbortAfterScheduleMonitor:
        def waitForAbort(self, timeout):
            calls.append("wait:%s" % timeout)
            return True

    def fake_window(key, value=None, clear=False):
        if value is not None:
            state[key] = value
            return None

        return state.get(key)

    monkeypatch.setattr(player, "stop_playback", lambda: calls.append("stop"))
    monkeypatch.setattr(player, "getPlayingFile", lambda: file_path)
    monkeypatch.setattr(
        player,
        "set_item",
        lambda current_file, current_item: calls.append("set_item"),
    )
    monkeypatch.setattr(
        player,
        "schedule_initial_seek",
        lambda current_file: calls.append("schedule"),
    )
    monkeypatch.setattr(
        player,
        "set_audio_subs",
        lambda audio, subtitle: calls.append("audio_subs"),
    )
    monkeypatch.setattr(player_module, "window", fake_window)
    monkeypatch.setattr(player_module, "settings", lambda key: False)
    monkeypatch.setattr(
        player_module.xbmc,
        "Monitor",
        lambda: AbortAfterScheduleMonitor(),
    )

    player.onPlayBackStarted()

    assert calls == ["stop", "set_item", "session", "schedule", "wait:2"]

from types import SimpleNamespace

from jellyfin_kodi import player as player_module


class RecordingJellyfin:
    def __init__(self):
        self.stop_calls = []

    def session_stop(self, data):
        self.stop_calls.append(data)

    def get_item(self, item_id):
        return {}


def make_player_item(current_position):
    jellyfin = RecordingJellyfin()
    item = {
        "Id": "item-id",
        "File": "/media/episode.mkv",
        "CurrentPosition": current_position,
        "Runtime": 1800,
        "MediaSourceId": "media-source-id",
        "PlaySessionId": "play-session-id",
        "PlayMethod": "DirectStream",
        "Server": SimpleNamespace(jellyfin=jellyfin),
    }
    return item, jellyfin


def patch_stop_side_effects(monkeypatch):
    monkeypatch.setattr(player_module, "window", lambda *args, **kwargs: False)
    monkeypatch.setattr(player_module, "translate_path", lambda path: "/tmp/jellyfin")
    monkeypatch.setattr(player_module.xbmcvfs, "exists", lambda path: False)


# When Kodi is still on the same file during stop, refresh the final position
# from the active player before reporting session_stop to Jellyfin.
def test_stop_playback_refreshes_current_position_before_session_stop(monkeypatch):
    patch_stop_side_effects(monkeypatch)
    item, jellyfin = make_player_item(current_position=947)
    player = player_module.Player()
    player.played = {item["File"]: item}

    monkeypatch.setattr(player, "get_playing_file", lambda: item["File"])
    monkeypatch.setattr(player, "getTime", lambda: 961)

    player.stop_playback()

    assert jellyfin.stop_calls == [
        {
            "ItemId": "item-id",
            "MediaSourceId": "media-source-id",
            "PositionTicks": 9610000000,
            "PlaySessionId": "play-session-id",
        }
    ]


# If playback teardown has already progressed far enough that getTime() fails,
# keep the cached position instead of failing or sending an invalid update.
def test_stop_playback_keeps_cached_position_when_position_refresh_fails(monkeypatch):
    patch_stop_side_effects(monkeypatch)
    item, jellyfin = make_player_item(current_position=947)
    player = player_module.Player()
    player.played = {item["File"]: item}

    monkeypatch.setattr(player, "get_playing_file", lambda: item["File"])

    def raise_runtime_error():
        raise RuntimeError("player already torn down")

    monkeypatch.setattr(player, "getTime", raise_runtime_error)

    player.stop_playback()

    assert jellyfin.stop_calls[0]["PositionTicks"] == 9470000000


# stop_playback() also runs when a new item starts, so only refresh the item
# that Kodi is actually still playing and do not borrow the new file's time.
def test_stop_playback_does_not_use_new_file_time_for_previous_session(monkeypatch):
    patch_stop_side_effects(monkeypatch)
    item, jellyfin = make_player_item(current_position=947)
    player = player_module.Player()
    player.played = {item["File"]: item}

    monkeypatch.setattr(player, "get_playing_file", lambda: "/media/new-episode.mkv")
    monkeypatch.setattr(player, "getTime", lambda: 23)

    player.stop_playback()

    assert jellyfin.stop_calls[0]["PositionTicks"] == 9470000000

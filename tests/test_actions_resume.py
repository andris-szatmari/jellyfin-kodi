from jellyfin_kodi.objects import actions as actions_module


class RecordingInfoTag:
    def __init__(self):
        self.calls = []

    def setResumePoint(self, resume_time, runtime):
        self.calls.append((resume_time, runtime))


class RecordingListItem:
    def __init__(self):
        self.info_tag = RecordingInfoTag()
        self.properties = {}

    def getVideoInfoTag(self):
        return self.info_tag

    def setProperty(self, key, value):
        self.properties[key] = value


# When resume is requested, set the info-tag resume point and resumetime
# for Kodi's UI display.  StartOffset is deliberately NOT set here; the
# actual playback position is enforced by the initial-seek thread in player.py.
def test_set_resume_properties_sets_info_tag_and_resumetime():
    listitem = RecordingListItem()

    actions_module.Actions.set_resume_properties(
        listitem,
        948.0,
        3389.05,
        actions_module.playutils.RESUME_INTENT_RESUME,
    )

    assert listitem.info_tag.calls == [(948.0, 3389.05)]
    assert listitem.properties["resumetime"] == "948.0"
    assert "StartOffset" not in listitem.properties


# If the user starts from the beginning, clear the resume point on the
# info tag so Kodi does not carry a stale offset into playback.
def test_set_resume_properties_clears_resume_point_on_startover():
    listitem = RecordingListItem()

    actions_module.Actions.set_resume_properties(
        listitem,
        948.0,
        3389.05,
        actions_module.playutils.RESUME_INTENT_STARTOVER,
    )

    assert listitem.info_tag.calls == [(0, 3389.05)]
    assert listitem.properties["resumetime"] == "0"
    assert "StartOffset" not in listitem.properties


def test_set_resume_properties_does_not_clear_without_explicit_startover():
    listitem = RecordingListItem()

    actions_module.Actions.set_resume_properties(
        listitem,
        948.0,
        3389.05,
        actions_module.playutils.RESUME_INTENT_NONE,
    )

    assert listitem.info_tag.calls == []
    assert listitem.properties == {}


class RecordingDialog:
    def __init__(self, selection, recorded):
        self.selection = selection
        self.recorded = recorded

    def contextmenu(self, options):
        self.recorded["options"] = options
        return self.selection


class FakeAPI:
    def __init__(self, item, server):
        self.item = item
        self.server = server

    @staticmethod
    def adjust_resume(resume_seconds):
        assert resume_seconds == 950.0
        return 948.0


def make_actions():
    actions = actions_module.Actions.__new__(actions_module.Actions)
    actions.api_client = object()
    actions.server = "https://server.test"
    actions.server_id = "server-id"
    actions.stack = []
    return actions


def test_resolve_resume_intent_uses_native_dialog_and_sets_offset(monkeypatch):
    actions = make_actions()
    recorded = {}
    item = {
        "MediaType": "Video",
        "UserData": {"PlaybackPositionTicks": 9500000000},
        "PlaybackInfo": {},
    }

    monkeypatch.setattr(actions_module.api, "API", FakeAPI)
    monkeypatch.setattr(
        actions_module.xbmcgui,
        "Dialog",
        lambda: RecordingDialog(0, recorded),
    )

    assert actions.resolve_resume_intent(item) is True
    assert item["ResumeIntent"] == actions_module.playutils.RESUME_INTENT_RESUME
    assert item["RequestedStartOffset"] == 948.0
    assert item["ForceInitialSeek"] is True
    assert item["PromptSource"] == actions_module.playutils.RESUME_PROMPT_SOURCE_NATIVE
    assert item["resumePlayback"] is True
    assert item["PlaybackInfo"]["CurrentPosition"] == 948.0
    assert item["PlaybackInfo"]["RequestedStartOffset"] == 948.0
    assert recorded["options"] == [
        "Resume from 0:15:48",
        "Start from beginning",
    ]


def test_resolve_resume_intent_returns_false_when_dialog_is_cancelled(monkeypatch):
    actions = make_actions()
    item = {
        "MediaType": "Video",
        "UserData": {"PlaybackPositionTicks": 9500000000},
        "PlaybackInfo": {},
    }

    monkeypatch.setattr(actions_module.api, "API", FakeAPI)
    monkeypatch.setattr(
        actions_module.xbmcgui,
        "Dialog",
        lambda: RecordingDialog(-1, {}),
    )

    assert actions.resolve_resume_intent(item) is False


def test_resolve_resume_intent_uses_kodi_resume_arg_without_prompt(monkeypatch):
    actions = make_actions()
    item = {
        "MediaType": "Video",
        "UserData": {"PlaybackPositionTicks": 9500000000},
        "PlaybackInfo": {},
    }

    monkeypatch.setattr(actions_module.api, "API", FakeAPI)
    monkeypatch.setattr(
        actions_module.sys,
        "argv",
        ["plugin://plugin.video.jellyfin", "7", "?mode=play", "resume:true"],
    )
    monkeypatch.setattr(
        actions_module.xbmcgui,
        "Dialog",
        lambda: (_ for _ in ()).throw(AssertionError("dialog should not be shown")),
    )

    assert actions.resolve_resume_intent(item) is True
    assert item["ResumeIntent"] == actions_module.playutils.RESUME_INTENT_RESUME
    assert item["RequestedStartOffset"] == 948.0
    assert item["ForceInitialSeek"] is True
    assert item["PromptSource"] == actions_module.playutils.RESUME_PROMPT_SOURCE_KODI


def test_resolve_resume_intent_treats_kodi_resume_false_as_startover(monkeypatch):
    actions = make_actions()
    item = {
        "MediaType": "Video",
        "UserData": {"PlaybackPositionTicks": 9500000000},
        "PlaybackInfo": {},
    }

    monkeypatch.setattr(actions_module.api, "API", FakeAPI)
    monkeypatch.setattr(
        actions_module.sys,
        "argv",
        ["plugin://plugin.video.jellyfin", "7", "?mode=play", "resume:false"],
    )
    monkeypatch.setattr(
        actions_module.xbmcgui,
        "Dialog",
        lambda: (_ for _ in ()).throw(AssertionError("dialog should not be shown")),
    )

    assert actions.resolve_resume_intent(item) is True
    assert item["ResumeIntent"] == actions_module.playutils.RESUME_INTENT_STARTOVER
    assert item["RequestedStartOffset"] == 0
    assert item["ForceInitialSeek"] is True
    assert item["PromptSource"] == actions_module.playutils.RESUME_PROMPT_SOURCE_KODI
    assert item["PlaybackInfo"]["CurrentPosition"] == 0


# The regular play path should pass the resume choice through set_listitem() so
# PlaybackInfo.CurrentPosition is populated for the launched session as well.
def test_set_playlist_marks_resume_requested_for_normal_playback(monkeypatch):
    actions = make_actions()

    recorded_seektime = {}
    listitem = object()
    item = {
        "MediaType": "Video",
        "Type": "Episode",
        "Id": "item-id",
        "Name": "Episode name",
        "UserData": {"PlaybackPositionTicks": 9480000000},
        "PlaybackInfo": {"Method": "DirectStream", "Path": "https://example.test/stream"},
    }

    monkeypatch.setattr(actions_module, "settings", lambda key: False)
    monkeypatch.setattr(
        actions,
        "resolve_resume_intent",
        lambda item_arg: (
            actions.apply_resume_intent(
                item_arg,
                actions_module.playutils.RESUME_INTENT_RESUME,
                948.0,
            )
            or True
        ),
    )
    monkeypatch.setattr(
        actions,
        "set_listitem",
        lambda item_arg, listitem_arg, db_id_arg, seektime_arg: recorded_seektime.update(
            {"seektime": seektime_arg, "item": item_arg, "listitem": listitem_arg}
        ),
    )
    monkeypatch.setattr(actions_module.playutils, "set_properties", lambda *args: None)

    actions.set_playlist(item, listitem, db_id=207, transcode=False)

    assert recorded_seektime["seektime"] is True
    assert recorded_seektime["item"] is item
    assert recorded_seektime["listitem"] is listitem
    assert item["PlaybackInfo"]["CurrentPosition"] == 948.0
    assert actions.stack == [["https://example.test/stream", listitem]]


def test_play_cancels_resolved_url_when_playlist_setup_is_cancelled(monkeypatch):
    actions = make_actions()
    item = {"Id": "item-id", "Name": "Episode name"}
    recorded = {}

    class DummyPlayUtils:
        def __init__(self, item, force_transcode, server_id, server, api_client):
            item["PlaybackInfo"] = {}

        @staticmethod
        def get_sources():
            return [object()]

        @staticmethod
        def select_source(sources):
            return sources[0]

        @staticmethod
        def set_external_subs(source, listitem):
            return None

    monkeypatch.setattr(actions_module.playutils, "PlayUtils", DummyPlayUtils)
    monkeypatch.setattr(actions, "set_playlist", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        actions_module.xbmcplugin,
        "setResolvedUrl",
        lambda handle, succeeded, listitem: recorded.update(
            {"handle": handle, "succeeded": succeeded}
        ),
    )
    monkeypatch.setattr(actions_module.sys, "argv", ["plugin://plugin.video.jellyfin", "7"])

    actions.play(item)

    assert recorded == {"handle": 7, "succeeded": False}

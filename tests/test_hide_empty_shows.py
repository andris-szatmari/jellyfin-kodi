import queue
from unittest.mock import ANY, MagicMock, patch

import pytest

from jellyfin_kodi.downloader import (
    get_episode_by_show,
    get_series_ids_with_importable_episodes,
    has_importable_episodes,
    is_importable_episode,
)
from jellyfin_kodi.entrypoint.service import Service
from jellyfin_kodi.full_sync import FullSync
from jellyfin_kodi.library import UpdateWorker
from jellyfin_kodi.objects.tvshows import TVShows


@pytest.mark.parametrize(
    "episode, expected",
    [
        (
            {
                "Path": "/show/ep1.mkv",
                "SeriesId": "show1",
                "LocationType": "FileSystem",
            },
            True,
        ),
        ({"Path": "", "SeriesId": "show1", "LocationType": "FileSystem"}, False),
        ({"SeriesId": "show1", "LocationType": "FileSystem"}, False),
        ({"Path": "/show/ep1.mkv", "LocationType": "FileSystem"}, False),
        ({"Path": "/show/ep1.mkv", "SeriesId": "show1"}, True),
        (
            {
                "Path": "/show/ep1.mkv",
                "SeriesId": "show1",
                "LocationType": "Virtual",
            },
            False,
        ),
    ],
)
def test_is_importable_episode(episode, expected):
    assert is_importable_episode(episode) is expected


@pytest.mark.parametrize(
    "pages, expected",
    [
        ([{"Items": []}], False),
        (
            [
                {"Items": [{"Path": "", "SeriesId": "show123"}]},
                {
                    "Items": [
                        {
                            "Path": "/show/ep2.mkv",
                            "SeriesId": "show123",
                            "LocationType": "FileSystem",
                        }
                    ]
                },
            ],
            True,
        ),
    ],
)
@patch("jellyfin_kodi.downloader._get_items")
def test_has_importable_episodes_result_mapping(mock_get_items, pages, expected):
    mock_get_items.return_value = pages

    assert has_importable_episodes("show123") is expected


@patch("jellyfin_kodi.downloader._get_items")
def test_has_importable_episodes_api_params(mock_get_items):
    mock_get_items.return_value = [{"Items": []}]

    has_importable_episodes("showABC")

    mock_get_items.assert_called_once()
    query = mock_get_items.call_args[0][0]

    assert "showABC" in query["url"]
    params = query["params"]
    assert params["Fields"] == "Path"
    assert params["IsMissing"] is False
    assert "IsVirtualUnaired" not in params
    assert "ExcludeLocationTypes" not in params
    assert params["EnableImages"] is False
    assert params["EnableUserData"] is False


@patch("jellyfin_kodi.downloader._get_items")
def test_get_series_ids_with_importable_episodes(mock_get_items):
    mock_get_items.return_value = [
        {
            "Items": [
                {"SeriesId": "show1", "Path": "/show1/ep1.mkv"},
                {"SeriesId": "show2", "Path": ""},
                {"SeriesId": "show3"},
                {"Path": "/missing-series-id.mkv"},
            ]
        },
        {"Items": [{"SeriesId": "show2", "Path": "/show2/ep3.mkv"}]},
    ]

    assert get_series_ids_with_importable_episodes("library123") == {
        "show1",
        "show2",
    }

    mock_get_items.assert_called_once()
    query = mock_get_items.call_args[0][0]
    assert query["url"] == "Users/{UserId}/Items"
    assert query["page_size"] == 200
    assert query["params"]["ParentId"] == "library123"
    assert query["params"]["IncludeItemTypes"] == "Episode"
    assert query["params"]["Recursive"] is True
    assert query["params"]["Fields"] == "Path"
    assert query["params"]["EnableTotalRecordCount"] is False
    assert query["params"]["ExcludeLocationTypes"] == "Virtual"


@patch("jellyfin_kodi.downloader._get_items")
def test_get_episode_by_show_does_not_filter_episodes(mock_get_items):
    mock_get_items.return_value = []

    list(get_episode_by_show("show123"))

    mock_get_items.assert_called_once()
    query = mock_get_items.call_args[0][0]

    assert query["url"] == "Shows/show123/Episodes"
    assert "IsMissing" not in query["params"]
    assert "IsVirtualUnaired" not in query["params"]


class FakeDatabaseContext(object):
    def __init__(self, db_file):
        self.db_file = db_file
        self.cursor = MagicMock()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeUpdateQueue(object):
    def __init__(self, items):
        self.items = list(items)
        self.task_done = MagicMock()

    def get(self, timeout=None):
        if self.items:
            return self.items.pop(0)
        raise queue.Empty


class FakeVideoDatabaseLocks(object):
    def __enter__(self):
        return MagicMock(), MagicMock()

    def __exit__(self, exc_type, exc, tb):
        return False


def make_full_sync():
    sync = FullSync.__new__(FullSync)
    sync.server = MagicMock()
    sync.direct_path = False
    sync.update_library = True
    sync.sync = {"RestorePoint": {"params": {}}}
    sync.video_database_locks = MagicMock(side_effect=FakeVideoDatabaseLocks)
    sync.tvshows_compare = MagicMock()
    return sync


def series_page(*shows):
    return {
        "Items": list(shows),
        "TotalRecordCount": len(shows),
        "RestorePoint": {"params": {"StartIndex": 0}},
    }


@patch("jellyfin_kodi.full_sync.settings", return_value=True)
@patch("jellyfin_kodi.full_sync.TVShows")
@patch("jellyfin_kodi.full_sync.server")
def test_full_sync_batch_match_avoids_fallback(
    mock_server,
    mock_tvshows,
    mock_settings,
):
    show = {"Id": "show1", "Name": "Normal Show"}
    mock_server.get_series_ids_with_importable_episodes.return_value = {"show1"}
    mock_server.get_items.return_value = [series_page(show)]
    mock_server.get_episode_by_show.return_value = []
    sync = make_full_sync()

    FullSync.tvshows.__wrapped__(sync, {"Id": "lib1", "Name": "TV"}, MagicMock())

    mock_server.has_importable_episodes.assert_not_called()
    mock_tvshows.return_value.tvshow.assert_called_once_with(show)
    assert mock_tvshows.return_value.item_ids == ["show1"]


@patch("jellyfin_kodi.full_sync.settings", return_value=True)
@patch("jellyfin_kodi.full_sync.TVShows")
@patch("jellyfin_kodi.full_sync.server")
def test_full_sync_episode_loop_uses_shared_predicate(
    mock_server,
    mock_tvshows,
    mock_settings,
):
    show = {"Id": "show1", "Name": "Normal Show"}
    rejected = {
        "Id": "virtual",
        "Path": "/ep1.mkv",
        "SeriesId": "show1",
        "LocationType": "Virtual",
        "Name": "Episode 1",
    }
    accepted = {
        "Id": "real",
        "Path": "/ep2.mkv",
        "SeriesId": "show1",
        "LocationType": "FileSystem",
        "Name": "Episode 2",
    }
    mock_server.get_series_ids_with_importable_episodes.return_value = {"show1"}
    mock_server.get_items.return_value = [series_page(show)]
    mock_server.get_episode_by_show.return_value = [
        {"Items": [rejected, accepted]}
    ]
    mock_server.is_importable_episode.side_effect = is_importable_episode
    sync = make_full_sync()

    FullSync.tvshows.__wrapped__(sync, {"Id": "lib1", "Name": "TV"}, MagicMock())

    mock_tvshows.return_value.episode.assert_called_once_with(accepted)


@patch("jellyfin_kodi.full_sync.settings", return_value=True)
@patch("jellyfin_kodi.full_sync.TVShows")
@patch("jellyfin_kodi.full_sync.server")
def test_full_sync_fallback_preserves_pooled_show_before_database_lock(
    mock_server,
    mock_tvshows,
    mock_settings,
):
    show = {"Id": "visible-show", "Name": "Pooled Show"}
    mock_server.get_series_ids_with_importable_episodes.return_value = {"pooled-id"}
    mock_server.get_items.return_value = [series_page(show)]
    mock_server.get_episode_by_show.return_value = []
    sync = make_full_sync()
    mock_server.has_importable_episodes.side_effect = lambda show_id: (
        not sync.video_database_locks.called
    )

    FullSync.tvshows.__wrapped__(sync, {"Id": "lib1", "Name": "TV"}, MagicMock())

    mock_server.has_importable_episodes.assert_called_once_with("visible-show")
    mock_tvshows.return_value.tvshow.assert_called_once_with(show)
    assert mock_tvshows.return_value.item_ids == ["visible-show"]


@patch("jellyfin_kodi.full_sync.settings", return_value=True)
@patch("jellyfin_kodi.full_sync.TVShows")
@patch("jellyfin_kodi.full_sync.server")
def test_full_sync_complete_negative_skips_and_reconciles(
    mock_server,
    mock_tvshows,
    mock_settings,
):
    show = {"Id": "empty-show", "Name": "Empty Show"}
    mock_server.get_series_ids_with_importable_episodes.return_value = set()
    mock_server.get_items.return_value = [series_page(show)]
    mock_server.has_importable_episodes.return_value = False
    sync = make_full_sync()

    FullSync.tvshows.__wrapped__(sync, {"Id": "lib1", "Name": "TV"}, MagicMock())

    mock_tvshows.return_value.tvshow.assert_not_called()
    assert mock_tvshows.return_value.item_ids == []
    sync.tvshows_compare.assert_called_once()


@pytest.mark.parametrize("failure_point", ["batch", "fallback"])
@patch("jellyfin_kodi.full_sync.settings", return_value=True)
@patch("jellyfin_kodi.full_sync.TVShows")
@patch("jellyfin_kodi.full_sync.server")
def test_full_sync_scan_failure_prevents_reconciliation(
    mock_server,
    mock_tvshows,
    mock_settings,
    failure_point,
):
    show = {"Id": "show1", "Name": "Existing Show"}
    if failure_point == "batch":
        mock_server.get_series_ids_with_importable_episodes.side_effect = RuntimeError(
            "batch failed"
        )
    else:
        mock_server.get_series_ids_with_importable_episodes.return_value = set()
        mock_server.get_items.return_value = [series_page(show)]
        mock_server.has_importable_episodes.side_effect = RuntimeError(
            "fallback failed"
        )
    sync = make_full_sync()

    with pytest.raises(RuntimeError, match="failed"):
        FullSync.tvshows.__wrapped__(
            sync, {"Id": "lib1", "Name": "TV"}, MagicMock()
        )

    sync.tvshows_compare.assert_not_called()


def test_episode_import_entry_skips_non_importable_item():
    tvshows = MagicMock()
    TVShows.episode.__wrapped__.__wrapped__(
        tvshows, {"Id": "missing-path", "SeriesId": "show1"}, None
    )

    tvshows.objects.map.assert_not_called()


@pytest.mark.parametrize("request_fails", [False, True])
@patch("jellyfin_kodi.library.window", new=MagicMock(return_value=False))
@patch("jellyfin_kodi.library.settings")
@patch("jellyfin_kodi.library.MusicVideos", new=MagicMock())
@patch("jellyfin_kodi.library.Movies", new=MagicMock())
@patch("jellyfin_kodi.library.TVShows")
@patch("jellyfin_kodi.library.Database")
def test_incremental_empty_check_is_failure_safe(
    mock_database,
    mock_tvshows,
    mock_settings,
    request_fails,
):
    mock_settings.side_effect = lambda key: True if key == "hideEmptyShows.bool" else False
    mock_database.side_effect = [
        FakeDatabaseContext("video"),
        FakeDatabaseContext("jellyfin"),
    ]
    update_queue = FakeUpdateQueue(
        [{"Type": "Series", "Id": "show123", "Name": "Existing Show"}]
    )
    lock = MagicMock()
    lock.__enter__.return_value = None
    lock.__exit__.return_value = False
    worker = UpdateWorker(
        update_queue,
        MagicMock(),
        lock,
        "video",
        server=MagicMock(),
        direct_path=False,
    )

    with patch(
        "jellyfin_kodi.library.has_importable_episodes",
        return_value=False,
        side_effect=RuntimeError("request failed") if request_fails else None,
    ):
        worker.run()

    if request_fails:
        mock_tvshows.return_value.remove.assert_not_called()
    else:
        mock_tvshows.return_value.remove.assert_called_once_with("show123")
    mock_tvshows.return_value.tvshow.assert_not_called()
    update_queue.task_done.assert_called_once_with()


@pytest.mark.parametrize(
    "new_value, libraries",
    [
        (True, ["lib1", "Mixed:lib2"]),
        (False, ["lib1"]),
    ],
)
@patch("jellyfin_kodi.entrypoint.service.window", new=MagicMock(return_value=False))
@patch("jellyfin_kodi.entrypoint.service.dialog", return_value=True)
@patch("jellyfin_kodi.entrypoint.service.library.get_sync")
@patch("jellyfin_kodi.entrypoint.service.settings")
def test_hide_empty_shows_setting_change_offers_update_sync(
    mock_settings,
    mock_get_sync,
    mock_dialog,
    new_value,
    libraries,
):
    mock_settings.side_effect = lambda key: {
        "logLevel": "1",
        "enableContext.bool": False,
        "enableContextTranscode.bool": False,
        "useDirectPaths": "0",
        "kodiCompanion.bool": False,
        "hideEmptyShows.bool": new_value,
    }[key]
    mock_get_sync.return_value = {"Whitelist": libraries}

    service = Service.__new__(Service)
    service.settings = {
        "log_level": "1",
        "enable_context": False,
        "enable_context_transcode": False,
        "mode": "0",
        "kodi_companion": False,
        "hide_empty_shows": not new_value,
    }
    service.library_thread = MagicMock()

    service.onSettingsChanged()

    mock_dialog.assert_called_once_with("yesno", "{jellyfin}", ANY)
    service.library_thread.add_library.assert_called_once_with(
        ",".join(libraries), True
    )
    assert service.settings["hide_empty_shows"] is new_value

from types import SimpleNamespace

from jellyfin_kodi.objects import movies as movies_module
from jellyfin_kodi.objects import tvshows as tvshows_module


class FakeAPI:
    def __init__(self, item, server):
        self.item = item
        self.server = server

    @staticmethod
    def resume_seconds(resume_ticks):
        assert resume_ticks == 9500000000
        return 950.0

    @staticmethod
    def get_playcount(played, playcount):
        return playcount


def make_server():
    return SimpleNamespace(
        auth=SimpleNamespace(
            server_id="server-id",
            get_server_info=lambda server_id: {"address": "https://server.test"},
        )
    )


def test_movie_userdata_writes_raw_resume_seconds_to_kodi(monkeypatch):
    movie = movies_module.Movies.__new__(movies_module.Movies)
    recorded = {}

    movie.server = make_server()
    movie.objects = SimpleNamespace(
        map=lambda item, kind: {
            "Resume": 9500000000,
            "Runtime": 18000000000,
            "Played": False,
            "PlayCount": 0,
            "DatePlayed": None,
            "Favorite": False,
            "Id": "item-id",
            "Title": "Title",
        }
    )
    movie.jellyfin_db = SimpleNamespace(update_reference=lambda *args: None)
    movie.get_tag = lambda *args: None
    movie.remove_tag = lambda *args: None
    movie.add_playstate = lambda *args: recorded.update({"args": args})

    monkeypatch.setattr(movies_module.api, "API", FakeAPI)
    monkeypatch.setattr(
        movies_module,
        "values",
        lambda obj, query: (obj["FileId"], obj["PlayCount"], obj["DatePlayed"], obj["Resume"]),
    )

    movies_module.Movies.userdata.__wrapped__.__wrapped__(
        movie,
        {"Id": "item-id"},
        e_item=(101, 202),
    )

    assert recorded["args"][3] == 950.0


def test_episode_userdata_writes_raw_resume_seconds_to_kodi(monkeypatch):
    tvshow = tvshows_module.TVShows.__new__(tvshows_module.TVShows)
    recorded = {}

    tvshow.server = make_server()
    tvshow.direct_path = True
    tvshow.objects = SimpleNamespace(
        map=lambda item, kind: {
            "Resume": 9500000000,
            "Runtime": 18000000000,
            "Played": False,
            "PlayCount": 0,
            "DatePlayed": None,
            "DateAdded": None,
            "Favorite": False,
            "Id": "item-id",
            "Title": "Title",
        }
    )
    tvshow.jellyfin_db = SimpleNamespace(update_reference=lambda *args: None)
    tvshow.get_tag = lambda *args: None
    tvshow.remove_tag = lambda *args: None
    tvshow.add_playstate = lambda *args: recorded.update({"args": args})

    monkeypatch.setattr(tvshows_module.api, "API", FakeAPI)
    monkeypatch.setattr(
        tvshows_module,
        "values",
        lambda obj, query: (obj["FileId"], obj["PlayCount"], obj["DatePlayed"], obj["Resume"]),
    )

    tvshows_module.TVShows.userdata.__wrapped__.__wrapped__(
        tvshow,
        {"Id": "item-id"},
        e_item=(101, 202, None, None, "episode"),
    )

    assert recorded["args"][3] == 950.0

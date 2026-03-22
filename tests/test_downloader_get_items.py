import os
import subprocess
import sys
from unittest.mock import patch

import pytest
import xbmc

from jellyfin_kodi import downloader as dl_module
from jellyfin_kodi.helper import wrapper as wrapper_module


class _FakeMonitor(xbmc.Monitor):
    def waitForAbort(self, timeout=-1):
        return False


@pytest.fixture(autouse=True)
def _neutralize_kodi(monkeypatch):
    monkeypatch.setattr(xbmc, "Monitor", _FakeMonitor)
    monkeypatch.setattr(
        wrapper_module,
        "window",
        lambda key, **kw: key == "jellyfin_online.bool",
    )


def make_page(start, count, total):
    return {
        "Items": [{"Id": str(start + i)} for i in range(count)],
        "TotalRecordCount": total,
    }


def configure_paging(monkeypatch, page_size=3, threads=2):
    monkeypatch.setattr(
        dl_module,
        "settings",
        lambda key: {
            "limitIndex": str(page_size),
            "limitThreads": str(threads),
        }.get(key, ""),
    )


def run_isolated_scenario(name):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        path for path in (repo_root, env.get("PYTHONPATH")) if path
    )

    subprocess.run(
        [sys.executable, __file__, name],
        check=True,
        capture_output=True,
        cwd=repo_root,
        env=env,
        text=True,
        timeout=3,
    )


def page_failure_scenario():
    def fake_get(url, params=None, server_id=None):
        if params.get("EnableTotalRecordCount"):
            return {"TotalRecordCount": 9}
        if params["StartIndex"] == 3:
            raise RuntimeError("page failed")
        return make_page(params["StartIndex"], 3, 9)

    def fake_settings(key):
        return {
            "limitIndex": "3",
            "limitThreads": "2",
        }.get(key, "")

    with patch.object(xbmc, "Monitor", _FakeMonitor), patch.object(
        wrapper_module,
        "window",
        lambda key, **kw: key == "jellyfin_online.bool",
    ), patch.object(dl_module, "_get", fake_get), patch.object(
        dl_module, "settings", fake_settings
    ):
        try:
            list(dl_module._get_items({"url": "Items", "params": {}}))
        except RuntimeError as error:
            assert "page failed" in str(error)
        else:
            raise AssertionError("Page failure was not propagated")


def early_close_scenario():
    page_calls = []

    def fake_get(url, params=None, server_id=None):
        if params.get("EnableTotalRecordCount"):
            return {"TotalRecordCount": 300}
        page_calls.append(params["StartIndex"])
        return make_page(params["StartIndex"], 3, 300)

    def fake_settings(key):
        return {
            "limitIndex": "3",
            "limitThreads": "3",
        }.get(key, "")

    with patch.object(xbmc, "Monitor", _FakeMonitor), patch.object(
        wrapper_module,
        "window",
        lambda key, **kw: key == "jellyfin_online.bool",
    ), patch.object(dl_module, "_get", fake_get), patch.object(
        dl_module, "settings", fake_settings
    ):
        generator = dl_module._get_items({"url": "Items", "params": {}})
        next(generator)
        generator.close()

    assert len(page_calls) <= 3


def test_get_items_raises_initial_count_failure(monkeypatch):
    def fake_get(url, params=None, server_id=None):
        raise RuntimeError("count failed")

    monkeypatch.setattr(dl_module, "_get", fake_get)
    configure_paging(monkeypatch)

    with pytest.raises(RuntimeError, match="count failed"):
        list(dl_module._get_items({"url": "Items", "params": {}}))


def test_get_items_raises_page_failure_and_finishes_cleanup():
    run_isolated_scenario("page-failure")


def test_get_items_raises_invalid_page_response(monkeypatch):
    def fake_get(url, params=None, server_id=None):
        if params.get("EnableTotalRecordCount"):
            return {"TotalRecordCount": 1}
        return None

    monkeypatch.setattr(dl_module, "_get", fake_get)
    configure_paging(monkeypatch)

    with pytest.raises(ValueError, match="Invalid page response"):
        list(dl_module._get_items({"url": "Items", "params": {}}))


def test_get_items_yields_every_page_once(monkeypatch):
    total = 12
    starts = []

    def fake_get(url, params=None, server_id=None):
        if params.get("EnableTotalRecordCount"):
            return {"TotalRecordCount": total}
        starts.append(params["StartIndex"])
        return make_page(params["StartIndex"], 3, total)

    monkeypatch.setattr(dl_module, "_get", fake_get)
    configure_paging(monkeypatch)

    collected = [
        item["Id"]
        for batch in dl_module._get_items({"url": "Items", "params": {}})
        for item in batch["Items"]
    ]

    assert sorted(starts) == [0, 3, 6, 9]
    assert sorted(collected, key=int) == [str(index) for index in range(total)]


def test_get_items_close_is_prompt_and_does_not_submit_every_page():
    run_isolated_scenario("early-close")


def test_importable_check_stops_with_bounded_request_window(monkeypatch):
    total = 300
    page_calls = []

    def fake_get(url, params=None, server_id=None):
        if params.get("EnableTotalRecordCount"):
            return {"TotalRecordCount": total}
        page_calls.append(params["StartIndex"])
        return {
            "Items": [
                {
                    "Path": "/show/episode.mkv",
                    "SeriesId": "show1",
                    "LocationType": "FileSystem",
                }
            ],
            "TotalRecordCount": total,
        }

    monkeypatch.setattr(dl_module, "_get", fake_get)
    configure_paging(monkeypatch, threads=3)

    assert dl_module.has_importable_episodes("show1") is True
    assert len(page_calls) <= 3


if __name__ == "__main__":
    scenarios = {
        "page-failure": page_failure_scenario,
        "early-close": early_close_scenario,
    }
    scenarios[sys.argv[1]]()

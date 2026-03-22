# -*- coding: utf-8 -*-
from __future__ import division, absolute_import, print_function, unicode_literals

#################################################################################################

import threading
import concurrent.futures
from datetime import date

import queue

import requests

from .helper import settings, stop, window, LazyLogger
from .jellyfin import Jellyfin
from .jellyfin import api
from .helper.exceptions import HTTPException

#################################################################################################

LOG = LazyLogger(__name__)

#################################################################################################


def get_jellyfinserver_url(handler):

    if handler.startswith("/"):

        handler = handler[1:]
        LOG.info("handler starts with /: %s", handler)

    return "{server}/%s" % handler


def _http(action, url, request=None, server_id=None):

    if request is None:
        request = {}

    request.update({"url": url, "type": action})
    return Jellyfin(server_id).http.request(request)


def _get(handler, params=None, server_id=None):
    return _http("GET", get_jellyfinserver_url(handler), {"params": params}, server_id)


def _post(handler, json=None, params=None, server_id=None):
    return _http(
        "POST",
        get_jellyfinserver_url(handler),
        {"params": params, "json": json},
        server_id,
    )


def _delete(handler, params=None, server_id=None):
    return _http(
        "DELETE", get_jellyfinserver_url(handler), {"params": params}, server_id
    )


def validate_view(library_id, item_id):
    """This confirms a single item from the library matches the view it belongs to.
    Used to detect grouped libraries.
    """
    try:
        result = _get(
            "Users/{UserId}/Items",
            {"ParentId": library_id, "Recursive": True, "Ids": item_id},
        )
    except Exception as error:
        LOG.exception(error)
        return False

    return bool(len(result["Items"]))


def get_single_item(parent_id, media):
    return _get(
        "Users/{UserId}/Items",
        {
            "ParentId": parent_id,
            "Recursive": True,
            "Limit": 1,
            "IncludeItemTypes": media,
        },
    )


def get_movies_by_boxset(boxset_id):

    for items in get_items(boxset_id, "Movie"):
        yield items


def get_episode_by_show(show_id):

    query = {
        "url": "Shows/%s/Episodes" % show_id,
        "params": {
            "EnableUserData": True,
            "EnableImages": True,
            "UserId": "{UserId}",
            "Fields": api.info(),
        },
    }
    for items in _get_items(query):
        yield items


def is_importable_episode(episode):
    """Return whether a raw Jellyfin episode can enter the Kodi import path."""
    return bool(
        episode.get("Path")
        and episode.get("SeriesId")
        and episode.get("LocationType") != "Virtual"
    )


def has_importable_episodes(show_id):
    """Return True if the show has at least one importable episode.

    Keep this predicate aligned with the actual episode import path.
    Path cannot be assumed to exist on every item, as established by #1096.
    """
    query = {
        "url": "Shows/%s/Episodes" % show_id,
        "params": {
            "UserId": "{UserId}",
            "EnableImages": False,
            "EnableUserData": False,
            "Fields": "Path",
            "IsMissing": False,
        },
    }

    for items in _get_items(query):
        for episode in items["Items"]:
            if is_importable_episode(episode):
                return True

    return False


def get_series_ids_with_importable_episodes(parent_id):
    """Return series ids that have at least one importable episode.

    Batch this per library during full sync to avoid one extra request
    per show when hideEmptyShows is enabled. The normal sync page size
    is tuned for full metadata imports; this scan only needs lightweight
    episode fields, so use a larger bounded page size to reduce round
    trips on larger libraries.
    """
    query = {
        "url": "Users/{UserId}/Items",
        "page_size": 200,
        "params": {
            "ParentId": parent_id,
            "IncludeItemTypes": "Episode",
            "Recursive": True,
            "Fields": "Path",
            "EnableImages": False,
            "EnableUserData": False,
            # _get_items enables this only for its initial count request.
            "EnableTotalRecordCount": False,
            "IsMissing": False,
            "IsVirtualUnaired": False,
            "ExcludeLocationTypes": "Virtual",
        },
    }
    series_ids = set()

    for items in _get_items(query):
        for episode in items["Items"]:
            if is_importable_episode(episode):
                series_ids.add(episode["SeriesId"])

    return series_ids


def get_episode_by_season(show_id, season_id):

    query = {
        "url": "Shows/%s/Episodes" % show_id,
        "params": {
            "SeasonId": season_id,
            "EnableUserData": True,
            "EnableImages": True,
            "UserId": "{UserId}",
            "Fields": api.info(),
        },
    }
    for items in _get_items(query):
        yield items


def get_item_count(parent_id, item_type=None):

    url = "Users/{UserId}/Items"

    query_params = {
        "ParentId": parent_id,
        "IncludeItemTypes": item_type,
        "EnableTotalRecordCount": True,
        "LocationTypes": "FileSystem,Remote,Offline",
        "Recursive": True,
        "Limit": 1,
    }

    result = _get(url, query_params)

    return result.get("TotalRecordCount", 1)


def get_items(parent_id, item_type=None, basic=False, params=None):

    query = {
        "url": "Users/{UserId}/Items",
        "params": {
            "ParentId": parent_id,
            "IncludeItemTypes": item_type,
            "SortBy": "SortName",
            "SortOrder": "Ascending",
            "Fields": api.basic_info() if basic else api.info(),
            "CollapseBoxSetItems": False,
            "IsVirtualUnaired": False,
            "EnableTotalRecordCount": False,
            "LocationTypes": "FileSystem,Remote,Offline",
            "IsMissing": False,
            "Recursive": True,
        },
    }
    if params:
        query["params"].update(params)

    for items in _get_items(query):
        yield items


def get_artists(parent_id=None):

    query = {
        "url": "Artists",
        "params": {
            "UserId": "{UserId}",
            "ParentId": parent_id,
            "SortBy": "SortName",
            "SortOrder": "Ascending",
            "Fields": api.music_info(),
            "CollapseBoxSetItems": False,
            "IsVirtualUnaired": False,
            "EnableTotalRecordCount": False,
            "LocationTypes": "FileSystem,Remote,Offline",
            "IsMissing": False,
            "Recursive": True,
        },
    }

    for items in _get_items(query):
        yield items


@stop
def _get_items(query, server_id=None):
    """query = {
        'url': string,
        'params': dict -- opt, include StartIndex to resume
        'page_size': int -- opt, override the default page size
    }
    """
    items = {"Items": [], "TotalRecordCount": 0, "RestorePoint": {}}

    limit = query.get("page_size")

    if limit is None:
        limit = min(int(settings("limitIndex") or 50), 50)

    dthreads = int(settings("limitThreads") or 3)

    url = query["url"]
    query.setdefault("params", {})
    params = query["params"]

    test_params = dict(params)
    test_params["Limit"] = 1
    test_params["EnableTotalRecordCount"] = True

    try:
        items["TotalRecordCount"] = _get(url, test_params, server_id=server_id)[
            "TotalRecordCount"
        ]
    except Exception as error:
        LOG.exception(
            "Failed to retrieve the server response %s: %s params:%s",
            url,
            error,
            params,
        )
        raise

    params.setdefault("StartIndex", 0)

    def get_query_params(params, start, count):
        params_copy = dict(params)
        params_copy["StartIndex"] = start
        params_copy["Limit"] = count
        return params_copy

    query_params = iter(
        get_query_params(params, offset, limit)
        for offset in range(params["StartIndex"], items["TotalRecordCount"], limit)
    )

    # Keep only a bounded window of requests submitted. A replacement is
    # scheduled after the consumer resumes from yield, preserving backpressure.
    with concurrent.futures.ThreadPoolExecutor(dthreads) as executor:
        pending = {}

        def submit_next():
            try:
                page_params = next(query_params)
            except StopIteration:
                return False

            pending[executor.submit(_get, url, page_params, server_id=server_id)] = (
                page_params
            )
            return True

        for _ in range(dthreads):
            if not submit_next():
                break

        try:
            while pending:
                completed, _ = concurrent.futures.wait(
                    pending, return_when=concurrent.futures.FIRST_COMPLETED
                )
                job = next(iter(completed))
                page_params = pending.pop(job)

                result = job.result()

                if not isinstance(result, dict) or not isinstance(
                    result.get("Items"), list
                ):
                    raise ValueError("Invalid page response from %s" % url)

                query["params"] = page_params

                # Mitigates #216 till the server validates the date provided is valid
                if result["Items"] and result["Items"][0].get("ProductionYear"):
                    try:
                        date(result["Items"][0]["ProductionYear"], 1, 1)
                    except ValueError:
                        LOG.info(
                            "#216 mitigation triggered. Setting ProductionYear to None"
                        )
                        result["Items"][0]["ProductionYear"] = None

                items["Items"].extend(result["Items"])
                # Using items to return data and communicate a restore point back to the callee is
                # a violation of the SRP. TODO: Separate responsibilities.
                items["RestorePoint"] = query
                yield items
                del items["Items"][:]
                submit_next()
        finally:
            for job in pending:
                job.cancel()


class GetItemWorker(threading.Thread):

    is_done = False

    def __init__(self, server, queue, output):

        self.server = server
        self.queue = queue
        self.output = output
        threading.Thread.__init__(self)

    def run(self):
        with requests.Session() as s:
            while True:
                try:
                    item_ids = self.queue.get(timeout=1)
                except queue.Empty:

                    self.is_done = True
                    LOG.info("--<[ q:download/%s ]", id(self))

                    return

                request = {
                    "type": "GET",
                    "handler": "Users/{UserId}/Items",
                    "params": {
                        "Ids": ",".join(str(x) for x in item_ids),
                        "Fields": api.info(),
                    },
                }

                try:
                    result = self.server.http.request(request, s)

                    for item in result["Items"]:

                        if item["Type"] in self.output:
                            self.output[item["Type"]].put(item)
                except HTTPException as error:
                    LOG.error("--[ http status: %s ]", error.status)

                    if error.status == "ServerUnreachable":
                        self.is_done = True

                        break

                except Exception as error:
                    LOG.exception(error)

                self.queue.task_done()

                if window("jellyfin_should_stop.bool"):
                    break

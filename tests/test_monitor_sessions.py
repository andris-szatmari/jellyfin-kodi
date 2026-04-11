from types import SimpleNamespace

from jellyfin_kodi import monitor as monitor_module


def make_monitor():
    monitor = monitor_module.Monitor.__new__(monitor_module.Monitor)
    monitor.device_id = "device-id"
    monitor.servers = []
    return monitor


def make_server(session):
    return SimpleNamespace(
        jellyfin=SimpleNamespace(get_device=lambda device_id: session),
        config=SimpleNamespace(data={}),
    )


def test_server_instance_tolerates_empty_device_session(monkeypatch):
    monitor = make_monitor()
    server = make_server([])

    monkeypatch.setattr(
        monitor_module,
        "Jellyfin",
        lambda server_id: SimpleNamespace(get_client=lambda: server),
    )

    monitor.server_instance()

    assert "app.session" not in server.config.data


def test_additional_users_tolerates_empty_device_session(monkeypatch):
    monitor = make_monitor()
    server = make_server([])
    cleared = []

    monkeypatch.setattr(
        monitor_module,
        "window",
        lambda key, value=None, clear=False: cleared.append((key, clear)),
    )

    monitor.additional_users(server)

    assert len(cleared) == 10

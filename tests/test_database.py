from report_writing.domain.models import DatabaseSettings
from report_writing.infra import postgres as pg
from report_writing.infra.postgres import _settings_from_config


def test_database_comes_from_local_yaml():
    settings = _settings_from_config()
    assert settings is not None
    assert settings.host == "127.0.0.1"
    assert settings.port == 13306
    assert settings.database == "anyuan"
    assert settings.user == "root"
    assert settings.ssl is False
    assert settings.password == "AnYuan_Root_564"


def _reset_pool() -> None:
    with pg._pool_lock:
        old = pg._pool
        pg._pool = None
        pg._pool_key = None
        pg._pool_created = 0
        pg._ready = False
    if old is not None:
        while True:
            try:
                c = old.get_nowait()
            except Exception:
                break
            try:
                c.close()
            except Exception:
                pass


def test_connection_pool_reuses_connections(monkeypatch):
    _reset_pool()
    creates: list[int] = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            return None

    class FakeConn:
        def __init__(self):
            creates.append(1)
            self.closed = False

        def ping(self, reconnect=True):
            if self.closed:
                raise RuntimeError("closed")

        def close(self):
            self.closed = True

        def cursor(self):
            return FakeCursor()

        def commit(self):
            return None

        def rollback(self):
            return None

    monkeypatch.setattr(pg, "_new_raw_connection", lambda settings: FakeConn())
    settings = DatabaseSettings(host="h", port=3306, database="d", user="u", password="p", ssl=False)

    with pg._connect(settings) as c1:
        c1.execute("SELECT 1")
    with pg._connect(settings) as c2:
        c2.execute("SELECT 1")
    with pg._connect(settings) as c3:
        c3.execute("SELECT 1")

    assert len(creates) == 1
    stats = pg.pool_stats()
    assert stats["created"] == 1
    assert stats["idle"] == 1
    _reset_pool()


def test_mysql_available_caches_probe(monkeypatch):
    pg._mysql_probe = None
    calls = {"n": 0}

    class FakeCursor:
        def execute(self, *args, **kwargs):
            return None

        def fetchone(self):
            return (1,)

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, *args, **kwargs):
            calls["n"] += 1
            return FakeCursor()

    monkeypatch.setattr(pg, "load_database_settings", lambda: DatabaseSettings(
        host="h", port=3306, database="d", user="u", password="p", ssl=False
    ))
    monkeypatch.setattr(pg, "_connect", lambda settings: FakeConn())

    assert pg.mysql_available() is True
    assert pg.mysql_available() is True
    assert calls["n"] == 1
    assert pg.mysql_available(force=True) is True
    assert calls["n"] == 2
    pg._mysql_probe = None

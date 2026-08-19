from codex_context.config import Settings


def test_resource_friendly_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.setenv("CODEX_CONTEXT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CODEX_CONTEXT_EMBED_THREADS", raising=False)
    monkeypatch.delenv("CODEX_CONTEXT_INDEX_BATCH", raising=False)
    monkeypatch.delenv("CODEX_CONTEXT_INDEX_PAUSE", raising=False)
    monkeypatch.delenv("CODEX_CONTEXT_STATUS_REFRESH", raising=False)

    settings = Settings.from_env()

    assert settings.embedding_threads == 2
    assert settings.index_batch_size == 32
    assert settings.index_pause_seconds == 0.08
    assert settings.status_refresh_seconds == 5.0


def test_resource_limits_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    monkeypatch.setenv("CODEX_CONTEXT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CODEX_CONTEXT_EMBED_THREADS", "4")
    monkeypatch.setenv("CODEX_CONTEXT_INDEX_BATCH", "48")
    monkeypatch.setenv("CODEX_CONTEXT_INDEX_PAUSE", "0.15")

    settings = Settings.from_env()

    assert settings.embedding_threads == 4
    assert settings.index_batch_size == 48
    assert settings.index_pause_seconds == 0.15

import os

from clip_agent.config import load_env_file, load_local_settings, save_local_settings


def test_load_env_file_does_not_override_existing_env(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=value-from-file\nNEW_VALUE='new-value'\n", encoding="utf-8")
    monkeypatch.setenv("EXISTING", "already-set")
    load_env_file(env_file)
    assert os.getenv("EXISTING") == "already-set"
    assert os.getenv("NEW_VALUE") == "new-value"


def test_load_env_file_fills_empty_existing_env(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("EMPTY_VALUE=from-file\n", encoding="utf-8")
    monkeypatch.setenv("EMPTY_VALUE", "")
    load_env_file(env_file)
    assert os.getenv("EMPTY_VALUE") == "from-file"


def test_load_env_file_strips_utf8_bom_from_key(tmp_path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("\ufeffBOM_VALUE=from-file\n", encoding="utf-8")
    monkeypatch.delenv("BOM_VALUE", raising=False)
    load_env_file(env_file)
    assert os.getenv("BOM_VALUE") == "from-file"


def test_local_settings_only_persist_allowed_values(tmp_path) -> None:
    path = tmp_path / "settings.json"
    save_local_settings(
        {
            "TRANSCRIPT_API_KEY": "test-key",
            "OPENAI_API_KEY": "openai-key",
            "NOT_ALLOWED": "ignored",
        },
        path,
    )
    assert load_local_settings(path) == {
        "TRANSCRIPT_API_KEY": "test-key",
        "OPENAI_API_KEY": "openai-key",
    }

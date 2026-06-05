import os

from clip_agent.config import load_env_file


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

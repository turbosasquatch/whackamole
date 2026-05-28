import yaml

from app.config import ConfigManager


def test_default_arr_timeout_is_300_seconds(tmp_path):
    manager = ConfigManager(str(tmp_path))

    cfg = manager.load()

    assert cfg.config_version == 2
    assert cfg.safety.arr_search_timeout_seconds == 300


def test_old_default_arr_timeout_migrates_to_300_seconds(tmp_path):
    manager = ConfigManager(str(tmp_path))
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "config_version": 1,
                "safety": {
                    "arr_search_timeout_seconds": 45,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    cfg = manager.load()

    assert cfg.config_version == 2
    assert cfg.safety.arr_search_timeout_seconds == 300


def test_custom_arr_timeout_survives_config_migration(tmp_path):
    manager = ConfigManager(str(tmp_path))
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "config_version": 1,
                "safety": {
                    "arr_search_timeout_seconds": 120,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    cfg = manager.load()

    assert cfg.config_version == 2
    assert cfg.safety.arr_search_timeout_seconds == 120

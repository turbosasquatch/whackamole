import yaml

from app.config import ConfigManager


def test_default_arr_timeout_is_300_seconds(tmp_path):
    manager = ConfigManager(str(tmp_path))

    cfg = manager.load()

    assert cfg.config_version == 4
    assert cfg.safety.arr_search_timeout_seconds == 300
    assert cfg.safety.arr_metadata_cache_seconds == 900
    assert cfg.safety.max_qui_poll_pages == 100
    assert cfg.safety.max_mediainfo_files_per_check == 8
    assert sorted(cfg.tracker_policies.keys()) == ["DP", "IHD", "ULCX"]


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

    assert cfg.config_version == 4
    assert cfg.safety.arr_search_timeout_seconds == 300
    assert cfg.tracker_policies["DP"]["banned_release_groups"] == []


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

    assert cfg.config_version == 4
    assert cfg.safety.arr_search_timeout_seconds == 120


def test_tracker_policy_config_migrates_missing_keys(tmp_path):
    manager = ConfigManager(str(tmp_path))
    manager.config_path.write_text(
        yaml.safe_dump(
            {
                "config_version": 2,
                "tracker_policies": {
                    "DP": {"banned_release_groups": ["BAD"]},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    cfg = manager.load()

    assert cfg.config_version == 4
    assert cfg.tracker_policies["DP"]["banned_release_groups"] == ["BAD"]
    assert cfg.tracker_policies["DP"]["ranked_release_groups"] == []
    assert "ULCX" in cfg.tracker_policies

import sys
import types


fake_config = types.ModuleType("app.config")
fake_config.settings = types.SimpleNamespace(YANDEX_TOKEN="")
fake_config.LegacyConfig = object
sys.modules.setdefault("app.config", fake_config)

fake_models = types.ModuleType("app.models")
fake_models.PostingSystemConfig = object
sys.modules.setdefault("app.models", fake_models)

fake_database = types.ModuleType("app.database")
fake_database.async_session_maker = None
sys.modules.setdefault("app.database", fake_database)

fake_seed_data = types.ModuleType("app.seed_data")
fake_seed_data.CLIENTS_SEED = []
sys.modules.setdefault("app.seed_data", fake_seed_data)

from app.services.config_db import preserve_profile_theme_keys


def test_preserve_profile_theme_keys_restores_missing_bindings():
    incoming = {
        "profiles": [
            {"username": "Bite1", "theme_key": "", "platforms": ["tiktok"]},
            {"username": "Bite2", "platforms": ["instagram"]},
        ]
    }
    current = {
        "profiles": [
            {"username": "bite1", "theme_key": "Bite"},
            {"username": "Bite2", "theme_key": "Smart"},
        ]
    }

    restored = preserve_profile_theme_keys(incoming, current)

    assert restored == 2
    assert incoming["profiles"][0]["theme_key"] == "Bite"
    assert incoming["profiles"][1]["theme_key"] == "Smart"


def test_preserve_profile_theme_keys_keeps_explicit_new_binding():
    incoming = {"profiles": [{"username": "Bite1", "theme_key": "MOLEKULAR"}]}
    current = {"profiles": [{"username": "Bite1", "theme_key": "Bite"}]}

    restored = preserve_profile_theme_keys(incoming, current)

    assert restored == 0
    assert incoming["profiles"][0]["theme_key"] == "MOLEKULAR"

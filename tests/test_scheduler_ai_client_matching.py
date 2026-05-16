import sys
import types
from types import SimpleNamespace


fake_config = types.ModuleType("app.config")
fake_config.settings = types.SimpleNamespace(YANDEX_TOKEN="")
fake_config.LegacyConfig = object
fake_config.SocialProfile = object
sys.modules.setdefault("app.config", fake_config)

from app.services.scheduler import has_ai_client


def test_has_ai_client_matches_brand_name_case_insensitive():
    clients = [
        SimpleNamespace(name="MOLEKULAR", regex="MOLEKULAR", quota=None),
    ]

    assert has_ai_client(
        clients,
        brand_name="molekular",
    )


def test_has_ai_client_matches_regex_against_extracted_brand_only():
    clients = [
        SimpleNamespace(name="Плати по миру ИИ", regex="Плати по миру ИИ", quota=None),
    ]

    assert has_ai_client(
        clients,
        brand_name="платипомируии",
    )


def test_has_ai_client_does_not_match_partial_brand():
    clients = [
        SimpleNamespace(name="Плати по миру", regex="Плати по миру", quota=None),
    ]

    assert not has_ai_client(
        clients,
        brand_name="платипомируии",
    )

import sys
import types
from types import SimpleNamespace


fake_config = types.ModuleType("app.config")
fake_config.settings = types.SimpleNamespace(YANDEX_TOKEN="")
fake_config.LegacyConfig = object
fake_config.SocialProfile = object
sys.modules.setdefault("app.config", fake_config)

from app.services.scheduler import has_ai_client


def test_has_ai_client_matches_client_folder_in_full_path():
    clients = [
        SimpleNamespace(name="MOLEKULAR", regex="MOLEKULAR", quota=None),
    ]

    assert has_ai_client(
        clients,
        brand_name="Крем для лица",
        video_path="disk:/ВИДЕО/SORA2/MOLEKULAR/Крем для лица/video.mp4",
    )


def test_has_ai_client_matches_regex_against_full_path():
    clients = [
        SimpleNamespace(name="Плати по миру ИИ", regex="Плати по миру ИИ", quota=None),
    ]

    assert has_ai_client(
        clients,
        brand_name="travel",
        video_path="disk:/ВИДЕО/Автор/PPM-Elena-travel/Плати по миру ИИ/video.mp4",
    )


def test_has_ai_client_matches_regex_with_spacing_differences():
    clients = [
        SimpleNamespace(name="Плати по миру ИИ", regex="Плати  по  миру   ИИ", quota=None),
    ]

    assert has_ai_client(
        clients,
        brand_name="travel",
        video_path="disk:/ВИДЕО/Автор/PPM-Elena-travel/Плати по миру ИИ/video.mp4",
    )

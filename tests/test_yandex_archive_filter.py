import sys
import types


fake_config = types.ModuleType("app.config")
fake_config.settings = types.SimpleNamespace(YANDEX_TOKEN="")
sys.modules.setdefault("app.config", fake_config)

from app.services.yandex import YandexDiskService


def test_filter_files_excludes_archive_folder_variants():
    service = YandexDiskService(token="test-token")
    files = [
        {"path": "disk:/ВИДЕО/Author/Bite/Bite/video.mp4"},
        {"path": "disk:/опубликовано/Bite/2026-02-03/old.mp4"},
        {"path": "disk:/опубликованно/Bite/2026-02-03/old-typo.mp4"},
    ]

    filtered = service._filter_files_by_folders(files, ["disk:/ВИДЕО"])

    assert [file["path"] for file in filtered] == ["disk:/ВИДЕО/Author/Bite/Bite/video.mp4"]

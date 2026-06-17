from app.services.repost_guard import RepostGuard, filter_recent_reposts, video_identity


def test_repost_guard_blocks_same_md5_with_different_path():
    guard = RepostGuard(paths=frozenset(), md5s=frozenset({"same-md5"}), file_names=frozenset())
    video = {"path": "disk:/video/new/place/reel.mp4", "name": "reel.mp4", "md5": "same-md5"}

    assert guard.blocks(video)


def test_repost_guard_blocks_same_file_name_when_md5_is_missing():
    guard = RepostGuard(paths=frozenset(), md5s=frozenset(), file_names=frozenset({"scenario_2572.mp4"}))
    video = {"path": "disk:/video/project/scenario_2572.mp4", "name": "", "md5": None}

    assert guard.blocks(video)


def test_filter_recent_reposts_returns_kept_videos_and_removed_count():
    guard = RepostGuard(paths=frozenset({"disk:/video/used.mp4"}), md5s=frozenset(), file_names=frozenset())
    videos = [
        {"path": "disk:/video/used.mp4", "name": "used.mp4"},
        {"path": "disk:/video/fresh.mp4", "name": "fresh.mp4"},
    ]

    kept, removed = filter_recent_reposts(videos, guard)

    assert removed == 1
    assert [video["path"] for video in kept] == ["disk:/video/fresh.mp4"]


def test_video_identity_normalizes_file_name_from_path():
    identity = video_identity({"path": "disk:/Video/Folder/Scenario_2572.MP4", "md5": ""})

    assert identity.file_name == "scenario_2572.mp4"

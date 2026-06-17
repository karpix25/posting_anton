from app.telegram_bot.video_dedupe import is_video_used, video_md5, video_path


def test_is_video_used_matches_existing_md5_with_different_path():
    video = {"path": "disk:/video/project/new-copy.mp4", "md5": "same-file"}

    assert is_video_used(video, used_paths=set(), used_md5s={"same-file"})


def test_is_video_used_falls_back_to_path_when_md5_is_missing():
    video = {"path": "disk:/video/project/video.mp4", "md5": None}

    assert is_video_used(video, used_paths={"disk:/video/project/video.mp4"}, used_md5s=set())


def test_video_keys_are_normalized_to_strings():
    video = {"path": 123, "md5": 456}

    assert video_path(video) == "123"
    assert video_md5(video) == "456"

from app.services.profile_status import merge_api_profiles_into_config


def test_merge_api_profiles_prunes_missing_profiles_but_preserves_theme_keys():
    config = {
        "profiles": [
            {"username": "Bite1", "theme_key": "Bite", "enabled": True},
            {"username": "Bite2", "theme_key": "Smart", "enabled": True},
            {"username": "Bite3", "theme_key": "", "enabled": True},
        ]
    }
    api_profiles = [
        {
            "username": "Bite1",
            "social_accounts": {
                "instagram": {"status": "connected"},
                "tiktok": {"status": "connected"},
            },
        },
        {
            "username": "Bite3",
            "social_accounts": {
                "tiktok": {"status": "connected"},
            },
        },
    ]

    summary = merge_api_profiles_into_config(
        config,
        api_profiles,
        upsert_missing_profiles=True,
    )

    usernames = [profile["username"] for profile in config["profiles"]]
    assert usernames == ["Bite1", "Bite3"]
    assert config["profiles"][0]["theme_key"] == "Bite"
    assert config["profiles"][1]["theme_key"] == ""
    assert summary["created"] == 0
    assert summary["updated"] == 2
    assert summary["removed"] == 1

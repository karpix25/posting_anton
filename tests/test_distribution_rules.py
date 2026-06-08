from app.telegram_bot.distribution_rules import (
    DistributionRuleAttempt,
    order_distribution_attempts,
    parse_folder_prefix_text,
)


def test_order_distribution_attempts_ignores_any_folder_when_specific_exists():
    attempts = [
        DistributionRuleAttempt(folder_prefix=(), daily_limit=1, daily_count=0, order_index=0),
        DistributionRuleAttempt(folder_prefix=("MOLEKULAR",), daily_limit=3, daily_count=1, order_index=1),
    ]

    ordered = order_distribution_attempts(attempts)

    assert [attempt.folder_prefix for attempt in ordered] == [("MOLEKULAR",)]


def test_order_distribution_attempts_balances_specific_folder_daily_usage():
    attempts = [
        DistributionRuleAttempt(folder_prefix=("Lethelux",), daily_limit=3, daily_count=1, order_index=0),
        DistributionRuleAttempt(folder_prefix=("MOLEKULAR",), daily_limit=3, daily_count=0, order_index=1),
    ]

    ordered = order_distribution_attempts(attempts)

    assert [attempt.folder_prefix for attempt in ordered] == [("MOLEKULAR",), ("Lethelux",)]


def test_order_distribution_attempts_keeps_any_folder_when_it_is_the_only_option():
    attempts = [
        DistributionRuleAttempt(folder_prefix=(), daily_limit=1, daily_count=0, order_index=0),
    ]

    ordered = order_distribution_attempts(attempts)

    assert [attempt.folder_prefix for attempt in ordered] == [()]


def test_parse_folder_prefix_text_treats_disabled_sentinel_as_empty():
    assert parse_folder_prefix_text("__disabled__") == ()

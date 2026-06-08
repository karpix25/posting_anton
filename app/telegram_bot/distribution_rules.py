from dataclasses import dataclass
from typing import Optional

DISABLED_FOLDER_PREFIX = "__disabled__"


@dataclass(frozen=True)
class DistributionRuleAttempt:
    folder_prefix: tuple[str, ...]
    daily_limit: int
    daily_count: int
    order_index: int
    rule_id: Optional[int] = None

    @property
    def usage_ratio(self) -> float:
        if self.daily_limit <= 0:
            return 1.0
        return self.daily_count / self.daily_limit

    @property
    def is_specific_folder(self) -> bool:
        return bool(self.folder_prefix)


def parse_folder_prefix_text(value: Optional[str]) -> tuple[str, ...]:
    text_value = str(value or "").strip()
    if not text_value or text_value == DISABLED_FOLDER_PREFIX:
        return ()
    return tuple(part.strip() for part in text_value.replace("\\", "/").split("/") if part.strip())


def format_folder_prefix(prefix: tuple[str, ...]) -> str:
    return " / ".join(prefix)


def order_distribution_attempts(attempts: list[DistributionRuleAttempt]) -> list[DistributionRuleAttempt]:
    specific_attempts = [attempt for attempt in attempts if attempt.is_specific_folder]
    if specific_attempts:
        return sorted(specific_attempts, key=_attempt_sort_key)
    return sorted(attempts, key=_attempt_sort_key)


def _attempt_sort_key(attempt: DistributionRuleAttempt) -> tuple[float, int, int, int]:
    return (
        attempt.usage_ratio,
        attempt.daily_count,
        -len(attempt.folder_prefix),
        attempt.order_index,
    )

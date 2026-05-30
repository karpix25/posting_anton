import re

CTA_TOKEN_RE = re.compile(r"\bcta\b", re.IGNORECASE)


def extract_cta_tokens(*values: str | None) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for value in values:
        for match in CTA_TOKEN_RE.finditer(value or ""):
            token = match.group(0)
            if token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return tokens


def build_cta_case_instruction(*values: str | None) -> str:
    tokens = extract_cta_tokens(*values)
    if not tokens:
        return ""

    token_list = ", ".join(f"«{token}»" for token in tokens)
    return (
        "\n\nВАЖНО ПРО CTA: если используешь CTA или упоминаешь call-to-action, "
        f"сохраняй регистр ровно как во входных данных: {token_list}. "
        "Не меняй CTA на cta или наоборот."
    )

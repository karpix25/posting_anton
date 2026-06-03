from app.services.product_article_rules import (
    build_article_instruction,
    ensure_article_line,
    extract_product_article_rule,
)


PROMPT = """
Маска для волос: (Артикул WB: #77718912). Стиль: тест.
Масло для душа : (Артикул WB: #855026122). Стиль: тест.
"""


def test_extract_product_article_rule_from_file_path():
    rule = extract_product_article_rule(PROMPT, "MOLEKULAR/Масло для душа/video.mp4")

    assert rule is not None
    assert rule.product_name == "Масло для душа"
    assert rule.article == "#855026122"


def test_build_article_instruction_contains_exact_required_line():
    rule = extract_product_article_rule(PROMPT, "MOLEKULAR/Масло для душа/video.mp4")
    instruction = build_article_instruction(rule)

    assert "Нашла на Wb — #855026122" in instruction


def test_ensure_article_line_inserts_before_lifehack():
    rule = extract_product_article_rule(PROMPT, "MOLEKULAR/Масло для душа/video.mp4")
    text = "Описание продукта.\n\nЛайфхак: использовать вечером.\n\n#уход #душ"

    result = ensure_article_line(text, rule)

    assert "Описание продукта.\n\nНашла на Wb — #855026122\n\nЛайфхак" in result


def test_ensure_article_line_updates_youtube_description_block():
    rule = extract_product_article_rule(PROMPT, "MOLEKULAR/Масло для душа/video.mp4")
    text = "[YT_TITLE]\nТест\n[/YT_TITLE]\n[YT_DESCRIPTION]\nОписание.\n\n#душ\n[/YT_DESCRIPTION]"

    result = ensure_article_line(text, rule)

    assert "Описание.\n\nНашла на Wb — #855026122\n\n#душ" in result

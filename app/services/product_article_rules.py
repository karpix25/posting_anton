import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote


@dataclass(frozen=True)
class ProductArticleRule:
    product_name: str
    article: str


PRODUCT_ARTICLE_RE = re.compile(
    r"^\s*([^:\n]+?)\s*:\s*\(Артикул\s+WB:\s*(#[A-Za-zА-Яа-яЁё0-9_-]+)\)",
    re.IGNORECASE | re.MULTILINE,
)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("ё", "е").casefold()).strip()


def extract_product_article_rule(prompt: str, file_path: str) -> Optional[ProductArticleRule]:
    normalized_path = _normalize(unquote(file_path or "").replace("/", " "))
    matches = [
        ProductArticleRule(product_name=match.group(1).strip(), article=match.group(2).strip())
        for match in PRODUCT_ARTICLE_RE.finditer(prompt or "")
    ]
    matches.sort(key=lambda item: len(item.product_name), reverse=True)
    for item in matches:
        if _normalize(item.product_name) in normalized_path:
            return item
    return None


def build_article_instruction(rule: Optional[ProductArticleRule]) -> str:
    if not rule:
        return ""
    return (
        "\n\nОБЯЗАТЕЛЬНЫЕ ДАННЫЕ ПРОДУКТА, уже извлеченные из пути файла:\n"
        f"- продукт: {rule.product_name}\n"
        f"- артикул Wildberries: {rule.article}\n"
        f"- в описании обязательно должна быть отдельная строка ровно: Нашла на Wb — {rule.article}\n"
        "Не заменяй, не пропускай и не выдумывай другой артикул."
    )


def ensure_article_line(text: str, rule: Optional[ProductArticleRule]) -> str:
    if not rule or not text:
        return text
    line = f"Нашла на Wb — {rule.article}"
    tagged = re.search(r"(\[YT_DESCRIPTION\]\s*)(.*?)(\s*\[/YT_DESCRIPTION\])", text, re.DOTALL)
    if tagged:
        if rule.article.lower() in tagged.group(2).lower():
            return text
        description = _insert_article_line(tagged.group(2), line)
        return text[: tagged.start(2)] + description + text[tagged.end(2) :]
    if rule.article.lower() in text.lower():
        return text
    return _insert_article_line(text, line)


def _insert_article_line(text: str, line: str) -> str:
    clean = (text or "").strip()
    if not clean:
        return line

    lifehack = re.search(r"(?im)^\s*Лайфхак\s*:", clean)
    if lifehack:
        return f"{clean[:lifehack.start()].rstrip()}\n\n{line}\n\n{clean[lifehack.start():].lstrip()}"

    hashtags = re.search(r"(?m)^\s*#", clean)
    if hashtags:
        return f"{clean[:hashtags.start()].rstrip()}\n\n{line}\n\n{clean[hashtags.start():].lstrip()}"

    parts = re.split(r"\n\s*\n", clean, maxsplit=1)
    if len(parts) == 2:
        return f"{parts[0].rstrip()}\n\n{line}\n\n{parts[1].lstrip()}"
    return f"{clean}\n\n{line}"

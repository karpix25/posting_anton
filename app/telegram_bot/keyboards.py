import hashlib
from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


BRAND_CALLBACK_PREFIX = "brand:"
FOLDER_CALLBACK_PREFIX = "folder:"
FOLDER_VIDEO_CALLBACK_PREFIX = "folder_video:"
BRAND_BUTTON_STYLES = ("primary", "success")
FOLDER_PAGE_SIZE = 20
_FOLDER_TOKEN_CACHE: dict[str, tuple[str, ...]] = {"root": ()}


def folder_token(prefix: Iterable[str]) -> str:
    parts = tuple(str(part) for part in prefix)
    if not parts:
        return "root"
    raw = "\0".join(parts)
    token = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    _FOLDER_TOKEN_CACHE[token] = parts
    return token


def resolve_folder_token(token: str) -> tuple[str, ...] | None:
    return _FOLDER_TOKEN_CACHE.get(token)


def folder_callback_data(prefix: Iterable[str], page: int = 0) -> str:
    return f"{FOLDER_CALLBACK_PREFIX}{folder_token(prefix)}:{max(page, 0)}"


def parse_folder_callback_data(data: str) -> tuple[tuple[str, ...] | None, int]:
    payload = data.removeprefix(FOLDER_CALLBACK_PREFIX)
    token, _, raw_page = payload.partition(":")
    try:
        page = max(int(raw_page or "0"), 0)
    except ValueError:
        page = 0
    return resolve_folder_token(token), page


def brands_keyboard(brands: list[str]) -> InlineKeyboardMarkup:
    sorted_brands = sorted(brands, key=lambda value: value.casefold())
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index, brand in enumerate(sorted_brands):
        row.append(
            InlineKeyboardButton(
                text=brand,
                callback_data=f"{BRAND_CALLBACK_PREFIX}{brand}",
                style=BRAND_BUTTON_STYLES[index % len(BRAND_BUTTON_STYLES)],
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def folder_navigation_keyboard(view, page: int = 0) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    total_children = len(view.children)
    total_pages = max((total_children + FOLDER_PAGE_SIZE - 1) // FOLDER_PAGE_SIZE, 1)
    page = min(max(page, 0), total_pages - 1)
    visible_children = view.children[page * FOLDER_PAGE_SIZE:(page + 1) * FOLDER_PAGE_SIZE]

    if view.prefix and view.total_videos:
        rows.append([
            InlineKeyboardButton(
                text=f"🎬 Выдать видео из этой папки ({view.total_videos})",
                callback_data=f"{FOLDER_VIDEO_CALLBACK_PREFIX}{folder_token(view.prefix)}",
                style="success",
            )
        ])

    row: list[InlineKeyboardButton] = []
    for index, folder in enumerate(visible_children):
        row.append(
            InlineKeyboardButton(
                text=f"📁 {folder.name} — {folder.video_count}",
                callback_data=folder_callback_data(folder.prefix),
                style=BRAND_BUTTON_STYLES[index % len(BRAND_BUTTON_STYLES)],
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    if total_pages > 1:
        pager_row: list[InlineKeyboardButton] = []
        if page > 0:
            pager_row.append(
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data=folder_callback_data(view.prefix, page - 1),
                    style="primary",
                )
            )
        pager_row.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data=folder_callback_data(view.prefix, page),
                style="primary",
            )
        )
        if page + 1 < total_pages:
            pager_row.append(
                InlineKeyboardButton(
                    text="Вперёд ▶️",
                    callback_data=folder_callback_data(view.prefix, page + 1),
                    style="primary",
                )
            )
        rows.append(pager_row)

    if view.prefix:
        rows.append([
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=folder_callback_data(view.prefix[:-1]),
                style="primary",
            ),
            InlineKeyboardButton(
                text="🏠 В начало",
                callback_data=folder_callback_data(()),
                style="primary",
            ),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu_keyboard(state: str = "idle") -> ReplyKeyboardMarkup:
    if state == "pending":
        rows = [[
            KeyboardButton(text="Мой отчет", style="primary"),
        ], [
            KeyboardButton(text="Отменить", style="danger"),
        ]]
        placeholder = "Заявка ожидает подтверждения администратора"
    elif state == "report":
        rows = [[
            KeyboardButton(text="Отправить ссылку", style="primary"),
            KeyboardButton(text="Мой отчет", style="primary"),
        ], [
            KeyboardButton(text="Отменить", style="danger"),
        ]]
        placeholder = "Пришлите ссылку на публикацию или выберите действие"
    else:
        rows = [[
            KeyboardButton(text="Подать заявку", style="success"),
            KeyboardButton(text="Мой отчет", style="primary"),
        ]]
        placeholder = "Пришлите ссылку YouTube или выберите действие"

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=placeholder,
    )

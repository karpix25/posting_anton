import hashlib
from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


BRAND_CALLBACK_PREFIX = "brand:"
FOLDER_CALLBACK_PREFIX = "folder:"
FOLDER_VIDEO_CALLBACK_PREFIX = "folder_video:"
BRAND_BUTTON_STYLES = ("primary", "success")
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


def brands_keyboard(brands: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for index, brand in enumerate(brands):
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


def folder_navigation_keyboard(view) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if view.prefix and view.total_videos:
        rows.append([
            InlineKeyboardButton(
                text=f"🎬 Выдать видео из этой папки ({view.total_videos})",
                callback_data=f"{FOLDER_VIDEO_CALLBACK_PREFIX}{folder_token(view.prefix)}",
                style="success",
            )
        ])

    row: list[InlineKeyboardButton] = []
    for index, folder in enumerate(view.children):
        row.append(
            InlineKeyboardButton(
                text=f"📁 {folder.name} — {folder.video_count}",
                callback_data=f"{FOLDER_CALLBACK_PREFIX}{folder_token(folder.prefix)}",
                style=BRAND_BUTTON_STYLES[index % len(BRAND_BUTTON_STYLES)],
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    if view.prefix:
        rows.append([
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"{FOLDER_CALLBACK_PREFIX}{folder_token(view.prefix[:-1])}",
                style="primary",
            ),
            InlineKeyboardButton(
                text="🏠 В начало",
                callback_data=f"{FOLDER_CALLBACK_PREFIX}root",
                style="primary",
            ),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    rows = [[
        KeyboardButton(text="Мой отчет", style="primary"),
        KeyboardButton(text="Отменить", style="danger"),
    ], [
        KeyboardButton(text="Структура", style="success"),
    ]]

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Пришлите ссылку YouTube или выберите действие",
    )

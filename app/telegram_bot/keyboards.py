from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


BRAND_CALLBACK_PREFIX = "brand:"


def brands_keyboard(brands: list[str]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for brand in brands:
        rows.append([
            InlineKeyboardButton(
                text=brand,
                callback_data=f"{BRAND_CALLBACK_PREFIX}{brand}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

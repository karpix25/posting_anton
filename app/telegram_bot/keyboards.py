from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


BRAND_CALLBACK_PREFIX = "brand:"
BRAND_BUTTON_STYLES = ("primary", "success")


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


def main_menu_keyboard(brands: list[str]) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    row: list[KeyboardButton] = []
    for index, brand in enumerate(brands):
        row.append(
            KeyboardButton(
                text=brand,
                style=BRAND_BUTTON_STYLES[index % len(BRAND_BUTTON_STYLES)],
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([
        KeyboardButton(text="Мой отчет", style="primary"),
        KeyboardButton(text="Отменить", style="danger"),
    ])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите бренд или пришлите ссылку YouTube",
    )

from aiogram.types import Message

from app.telegram_bot.keyboards import action_inline_keyboard, main_menu_keyboard
from app.telegram_bot.service import STATUS_APPROVED, STATUS_PENDING_APPROVAL, get_open_request


def menu_state_for_request(request) -> str:
    if not request:
        return "idle"
    if request.status in (STATUS_PENDING_APPROVAL, STATUS_APPROVED):
        return "pending"
    return "report"


async def user_menu_keyboard(telegram_user_id: int):
    request = await get_open_request(telegram_user_id)
    return main_menu_keyboard(menu_state_for_request(request))


async def user_action_keyboard(telegram_user_id: int):
    request = await get_open_request(telegram_user_id)
    return action_inline_keyboard(menu_state_for_request(request))


async def message_menu_keyboard(message: Message, state: str | None = None):
    if state:
        return main_menu_keyboard(state)
    if not message.from_user:
        return main_menu_keyboard()
    return await user_menu_keyboard(message.from_user.id)

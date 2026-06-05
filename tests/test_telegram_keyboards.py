from app.telegram_bot.keyboards import action_inline_keyboard, main_menu_keyboard


def _inline_texts(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def _reply_texts(markup):
    return [button.text for row in markup.keyboard for button in row]


def test_report_inline_keyboard_does_not_offer_new_request():
    texts = _inline_texts(action_inline_keyboard("report"))

    assert "Отправить ссылку" in texts
    assert "Мой отчет" in texts
    assert "Подать заявку" not in texts


def test_report_reply_keyboard_does_not_offer_new_request():
    texts = _reply_texts(main_menu_keyboard("report"))

    assert "Отправить ссылку" in texts
    assert "Мой отчет" in texts
    assert "Подать заявку" not in texts


def test_idle_keyboard_still_offers_new_request():
    assert "Подать заявку" in _inline_texts(action_inline_keyboard("idle"))
    assert "Подать заявку" in _reply_texts(main_menu_keyboard("idle"))

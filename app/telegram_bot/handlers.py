import logging
import os
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.config import settings
from app.telegram_bot.keyboards import BRAND_CALLBACK_PREFIX, brands_keyboard, main_menu_keyboard
from app.telegram_bot.service import (
    accept_publication_report,
    admin_report,
    cancel_pending_request,
    download_video_to_temp,
    get_pending_request,
    is_youtube_url,
    list_brands,
    prepare_random_video,
    user_report,
)

logger = logging.getLogger(__name__)
router = Router()


def _admin_ids() -> set[int]:
    ids = set()
    for raw in (settings.TELEGRAM_ADMIN_IDS or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ids.add(int(raw))
        except ValueError:
            logger.warning("Invalid TELEGRAM_ADMIN_IDS item: %s", raw)
    return ids


@router.message(Command("start"))
async def start(message: Message):
    brands = await list_brands()
    if not brands:
        await message.answer("Бренды пока не настроены.")
        return

    await message.answer("Выберите бренд:", reply_markup=main_menu_keyboard(brands))
    await message.answer("Быстрые кнопки:", reply_markup=brands_keyboard(brands))


@router.message(Command("report"))
async def report(message: Message):
    if not message.from_user:
        return
    stats = await user_report(message.from_user.id)
    pending = stats["requested"] - stats["reported"]
    username = stats.get("telegram_username") or message.from_user.username
    full_name = stats.get("telegram_full_name") or " ".join(
        part for part in [message.from_user.first_name, message.from_user.last_name] if part
    )
    user_label = f"@{username}" if username else (full_name or str(message.from_user.id))
    brands = await list_brands()
    await message.answer(
        "Ваш отчет:\n"
        f"Пользователь: {user_label}\n"
        f"Telegram ID: {message.from_user.id}\n"
        f"Запрошено видео: {stats['requested']}\n"
        f"Отчитано ссылками: {stats['reported']}\n"
        f"Ожидает отчета: {pending}",
        reply_markup=main_menu_keyboard(brands),
    )


@router.message(Command("admin_report"))
async def report_admin(message: Message):
    if not message.from_user or message.from_user.id not in _admin_ids():
        await message.answer("Команда доступна только администратору.")
        return

    rows = await admin_report()
    if not rows:
        await message.answer("Отчет пока пуст.")
        return

    lines = ["Общий отчет:"]
    for row in rows[:50]:
        username = row.get("telegram_username")
        name = row.get("telegram_full_name")
        label = f"@{username}" if username else (name or str(row["telegram_user_id"]))
        requested = int(row["requested"])
        reported = int(row["reported"])
        lines.append(
            f"{label} (ID {row['telegram_user_id']}): "
            f"запросил {requested}, отчитался {reported}, ожидает {requested - reported}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("cancel"))
async def cancel(message: Message):
    if not message.from_user:
        return
    brands = await list_brands()
    cancelled = await cancel_pending_request(message.from_user.id)
    if cancelled:
        await message.answer(
            "Ожидание ссылки отменено. Можно запросить новое видео.",
            reply_markup=main_menu_keyboard(brands),
        )
    else:
        await message.answer("У вас нет видео, ожидающего отчета.", reply_markup=main_menu_keyboard(brands))


@router.callback_query(F.data.startswith(BRAND_CALLBACK_PREFIX))
async def select_brand(callback: CallbackQuery):
    if not callback.message or not callback.from_user or not callback.data:
        return

    brand = callback.data.removeprefix(BRAND_CALLBACK_PREFIX)
    await callback.answer()
    await send_brand_video(callback.message, callback.from_user, brand)


async def send_brand_video(message: Message, user, brand: str):
    brands = await list_brands()
    await message.answer(f"Ищу случайное видео для {brand}...", reply_markup=main_menu_keyboard(brands))

    try:
        prepared = await prepare_random_video(
            brand=brand,
            telegram_user_id=user.id,
            telegram_username=user.username,
            telegram_full_name=" ".join(
                part for part in [user.first_name, user.last_name] if part
            ),
        )
    except RuntimeError as exc:
        if str(exc) == "pending_report":
            pending = await get_pending_request(user.id)
            name = pending.video_name if pending else "предыдущее видео"
            await message.answer(
                f"Сначала пришлите ссылку на опубликованное видео для: {name}\n"
                "Или используйте /cancel, если нужно отменить ожидание."
            )
            return
        raise
    except LookupError:
        await message.answer("Свободных видео для этого бренда сейчас не нашлось.")
        return
    except Exception as exc:
        logger.exception("Failed to prepare Telegram video")
        await message.answer(f"Не удалось подготовить видео: {exc}")
        return

    request = prepared.request
    temp_path = None
    if prepared.should_send_as_link:
        size_mb = prepared.size / 1024 / 1024 if prepared.size else 0
        await message.answer(
            f"Файл больше 50 МБ ({size_mb:.1f} МБ), поэтому отправляю прямую ссылку:\n"
            f"{prepared.download_link}"
        )
    else:
        try:
            await message.answer("Скачиваю файл, чтобы отправить его без сжатия...")
            temp_path = await download_video_to_temp(prepared.download_link, request.video_name)
            await message.answer_document(
                FSInputFile(temp_path, filename=request.video_name),
                caption=request.video_name[:1024],
            )
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    logger.warning("Failed to remove temporary Telegram video file: %s", temp_path)

    await message.answer("Title:")
    await message.answer(f"<code>{escape(request.youtube_title or '')}</code>")
    await message.answer("Description:")
    await message.answer(f"<pre>{escape(request.youtube_description or '')}</pre>")
    await message.answer(
        "После публикации пришлите ссылку на YouTube-видео ответным сообщением.",
        reply_markup=main_menu_keyboard(brands),
    )


@router.message(F.text)
async def handle_text(message: Message):
    if not message.from_user or not message.text:
        return

    brands = await list_brands()
    if message.text == "Мой отчет":
        await report(message)
        return
    if message.text == "Отменить":
        await cancel(message)
        return
    if message.text in brands:
        await send_brand_video(message, message.from_user, message.text)
        return

    pending = await get_pending_request(message.from_user.id)
    if not pending:
        await message.answer("Выберите бренд на клавиатуре, чтобы получить видео.", reply_markup=main_menu_keyboard(brands))
        return

    url = message.text.strip()
    if not is_youtube_url(url):
        await message.answer(
            "Жду ссылку на YouTube, например https://youtube.com/shorts/...",
            reply_markup=main_menu_keyboard(brands),
        )
        return

    request = await accept_publication_report(message.from_user.id, url)
    if request.status == "archived":
        await message.answer(
            "Спасибо, отчет принят. Видео перенесено в папку опубликовано.",
            reply_markup=main_menu_keyboard(brands),
        )
    else:
        await message.answer(
            "Спасибо, отчет принят. Видео помечено как опубликованное, но перенос на Яндекс.Диске не удался. "
            "Администратор сможет проверить ошибку в логах.",
            reply_markup=main_menu_keyboard(brands),
        )

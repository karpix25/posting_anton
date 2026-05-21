import logging
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.config import settings
from app.telegram_bot.keyboards import BRAND_CALLBACK_PREFIX, brands_keyboard
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

    await message.answer("Выберите бренд:", reply_markup=brands_keyboard(brands))


@router.message(Command("report"))
async def report(message: Message):
    if not message.from_user:
        return
    stats = await user_report(message.from_user.id)
    pending = stats["requested"] - stats["reported"]
    await message.answer(
        "Ваш отчет:\n"
        f"Запрошено видео: {stats['requested']}\n"
        f"Отчитано ссылками: {stats['reported']}\n"
        f"Ожидает отчета: {pending}"
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
        lines.append(f"{label}: запросил {requested}, отчитался {reported}, ожидает {requested - reported}")
    await message.answer("\n".join(lines))


@router.message(Command("cancel"))
async def cancel(message: Message):
    if not message.from_user:
        return
    cancelled = await cancel_pending_request(message.from_user.id)
    if cancelled:
        await message.answer("Ожидание ссылки отменено. Можно запросить новое видео.")
    else:
        await message.answer("У вас нет видео, ожидающего отчета.")


@router.callback_query(F.data.startswith(BRAND_CALLBACK_PREFIX))
async def select_brand(callback: CallbackQuery):
    if not callback.message or not callback.from_user or not callback.data:
        return

    brand = callback.data.removeprefix(BRAND_CALLBACK_PREFIX)
    await callback.answer()
    await callback.message.answer(f"Ищу случайное видео для {brand}...")

    try:
        prepared = await prepare_random_video(
            brand=brand,
            telegram_user_id=callback.from_user.id,
            telegram_username=callback.from_user.username,
            telegram_full_name=" ".join(
                part for part in [callback.from_user.first_name, callback.from_user.last_name] if part
            ),
        )
    except RuntimeError as exc:
        if str(exc) == "pending_report":
            pending = await get_pending_request(callback.from_user.id)
            name = pending.video_name if pending else "предыдущее видео"
            await callback.message.answer(
                f"Сначала пришлите ссылку на опубликованное видео для: {name}\n"
                "Или используйте /cancel, если нужно отменить ожидание."
            )
            return
        raise
    except LookupError:
        await callback.message.answer("Свободных видео для этого бренда сейчас не нашлось.")
        return
    except Exception as exc:
        logger.exception("Failed to prepare Telegram video")
        await callback.message.answer(f"Не удалось подготовить видео: {exc}")
        return

    request = prepared.request
    temp_path = None
    if prepared.should_send_as_link:
        size_mb = prepared.size / 1024 / 1024 if prepared.size else 0
        await callback.message.answer(
            f"Файл больше 50 МБ ({size_mb:.1f} МБ), поэтому отправляю прямую ссылку:\n"
            f"{prepared.download_link}"
        )
    else:
        try:
            await callback.message.answer("Скачиваю файл, чтобы отправить его без сжатия...")
            temp_path = await download_video_to_temp(prepared.download_link, request.video_name)
            await callback.message.answer_document(
                FSInputFile(temp_path, filename=request.video_name),
                caption=request.video_name[:1024],
            )
        finally:
            if temp_path:
                try:
                    os.remove(temp_path)
                except OSError:
                    logger.warning("Failed to remove temporary Telegram video file: %s", temp_path)

    await callback.message.answer(
        "Title:\n"
        f"{request.youtube_title}\n\n"
        "Description:\n"
        f"{request.youtube_description}\n\n"
        "После публикации пришлите ссылку на YouTube-видео ответным сообщением."
    )


@router.message(F.text)
async def handle_text(message: Message):
    if not message.from_user or not message.text:
        return

    pending = await get_pending_request(message.from_user.id)
    if not pending:
        await message.answer("Выберите бренд через /start, чтобы получить видео.")
        return

    url = message.text.strip()
    if not is_youtube_url(url):
        await message.answer("Жду ссылку на YouTube, например https://youtube.com/shorts/...")
        return

    request = await accept_publication_report(message.from_user.id, url)
    if request.status == "archived":
        await message.answer("Спасибо, отчет принят. Видео перенесено в папку опубликовано.")
    else:
        await message.answer(
            "Спасибо, отчет принят. Видео помечено как опубликованное, но перенос на Яндекс.Диске не удался. "
            "Администратор сможет проверить ошибку в логах."
        )

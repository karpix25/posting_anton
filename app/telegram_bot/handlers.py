import logging
import os
from html import escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.config import settings
from app.telegram_bot.keyboards import BRAND_CALLBACK_PREFIX, brands_keyboard, main_menu_keyboard
from app.telegram_bot.keyboards import (
    FOLDER_CALLBACK_PREFIX,
    FOLDER_VIDEO_CALLBACK_PREFIX,
    folder_navigation_keyboard,
    parse_folder_callback_data,
    resolve_folder_token,
)
from app.telegram_bot.service import (
    accept_publication_report,
    admin_report,
    build_video_inventory_text,
    cancel_pending_request,
    download_video_to_temp,
    get_video_folder_view,
    get_pending_request,
    is_youtube_url,
    list_brands,
    prepare_random_video,
    prepare_random_video_from_folder,
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
    await message.answer("Системные кнопки снизу обновлены.", reply_markup=main_menu_keyboard())
    await send_folder_navigation(message, ())


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
    await message.answer(
        "Ваш отчет:\n"
        f"Пользователь: {user_label}\n"
        f"Telegram ID: {message.from_user.id}\n"
        f"Запрошено видео: {stats['requested']}\n"
        f"Отчитано ссылками: {stats['reported']}\n"
        f"Ожидает отчета: {pending}",
        reply_markup=main_menu_keyboard(),
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


@router.message(Command("inventory"))
async def inventory(message: Message):
    await send_inventory(message)


@router.message(Command("cancel"))
async def cancel(message: Message):
    if not message.from_user:
        return
    cancelled = await cancel_pending_request(message.from_user.id)
    if cancelled:
        await message.answer(
            "Ожидание ссылки отменено. Можно запросить новое видео.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await message.answer("У вас нет видео, ожидающего отчета.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data.startswith(BRAND_CALLBACK_PREFIX))
async def select_brand(callback: CallbackQuery):
    if not callback.message or not callback.from_user or not callback.data:
        return

    brand = callback.data.removeprefix(BRAND_CALLBACK_PREFIX)
    await callback.answer()
    await send_brand_video(callback.message, callback.from_user, brand)


@router.callback_query(F.data.startswith(FOLDER_CALLBACK_PREFIX))
async def select_folder(callback: CallbackQuery):
    if not callback.message or not callback.data:
        return

    prefix, page = parse_folder_callback_data(callback.data)
    if prefix is None:
        await callback.answer("Кнопка устарела, обновляю список.", show_alert=True)
        await send_folder_navigation(callback.message, ())
        return

    await callback.answer()
    await send_folder_navigation(callback.message, prefix, page=page)


@router.callback_query(F.data.startswith(FOLDER_VIDEO_CALLBACK_PREFIX))
async def select_folder_video(callback: CallbackQuery):
    if not callback.message or not callback.from_user or not callback.data:
        return

    token = callback.data.removeprefix(FOLDER_VIDEO_CALLBACK_PREFIX)
    prefix = resolve_folder_token(token)
    if prefix is None:
        await callback.answer("Кнопка устарела, обновите список через /start.", show_alert=True)
        return

    await callback.answer()
    await send_folder_video(callback.message, callback.from_user, prefix)


async def send_brand_video(message: Message, user, brand: str):
    brands = await list_brands()
    await message.answer(f"Ищу случайное видео для {brand}...", reply_markup=main_menu_keyboard())

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
        reply_markup=main_menu_keyboard(),
    )
    await message.answer("Можно выбрать следующий бренд:", reply_markup=brands_keyboard(brands))


async def send_folder_video(message: Message, user, folder_prefix: tuple[str, ...]):
    label = " / ".join(folder_prefix)
    await message.answer(f"Ищу случайное видео в папке: {label}", reply_markup=main_menu_keyboard())

    try:
        prepared = await prepare_random_video_from_folder(
            folder_prefix=folder_prefix,
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
        await message.answer("Свободных видео в этой папке сейчас не нашлось.")
        return
    except Exception as exc:
        logger.exception("Failed to prepare Telegram folder video")
        await message.answer(f"Не удалось подготовить видео: {exc}")
        return

    await send_prepared_video(message, prepared)
    await send_folder_navigation(message, folder_prefix)


async def send_prepared_video(message: Message, prepared):
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
        reply_markup=main_menu_keyboard(),
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
    if message.text == "Структура":
        await send_folder_navigation(message, ())
        return
    if message.text in brands:
        await send_brand_video(message, message.from_user, message.text)
        return

    pending = await get_pending_request(message.from_user.id)
    if not pending:
        await message.answer("Выберите папку кнопками выше или нажмите /start.", reply_markup=main_menu_keyboard())
        return

    url = message.text.strip()
    if not is_youtube_url(url):
        await message.answer(
            "Жду ссылку на YouTube, например https://youtube.com/shorts/...",
            reply_markup=main_menu_keyboard(),
        )
        return

    request = await accept_publication_report(message.from_user.id, url)
    if request.status == "archived":
        await message.answer(
            "Спасибо, отчет принят. Видео перенесено в папку опубликовано.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        await message.answer(
            "Спасибо, отчет принят. Видео помечено как опубликованное, но перенос на Яндекс.Диске не удался. "
            "Администратор сможет проверить ошибку в логах.",
            reply_markup=main_menu_keyboard(),
        )


async def send_folder_navigation(message: Message, prefix: tuple[str, ...], page: int = 0):
    try:
        view = await get_video_folder_view(prefix)
    except Exception as exc:
        logger.exception("Failed to build Telegram folder navigation")
        await message.answer(f"Не удалось собрать папки: {exc}", reply_markup=main_menu_keyboard())
        return

    if not view.children and not view.total_videos:
        await message.answer("В этой папке видео не нашлись.", reply_markup=main_menu_keyboard())
        return

    if view.prefix:
        text = f"Папка:\n{view.title}\n\nВидео внутри: {view.total_videos}"
    else:
        text = "Выберите папку:"
    await message.answer(text, reply_markup=folder_navigation_keyboard(view, page=page))


async def send_inventory(message: Message):
    await message.answer("Сканирую структуру видео на диске...", reply_markup=main_menu_keyboard())
    try:
        text = await build_video_inventory_text()
    except Exception as exc:
        logger.exception("Failed to build Telegram video inventory")
        await message.answer(f"Не удалось собрать структуру: {exc}", reply_markup=main_menu_keyboard())
        return

    for chunk in split_telegram_text(text):
        await message.answer(f"<pre>{escape(chunk)}</pre>", reply_markup=main_menu_keyboard())


def split_telegram_text(text: str, limit: int = 3500) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        next_value = f"{current}\n{line}" if current else line
        if len(next_value) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = next_value
    if current:
        chunks.append(current)
    return chunks

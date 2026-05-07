"""
╔══════════════════════════════════════════╗
║        🎱 BINGO DATING BOT 🎱            ║
║  aiogram 3.x | asyncpg | PostgreSQL      ║
╚══════════════════════════════════════════╝

Структура файла:
  1. Конфигурация (токены, file_id шаблонов)
  2. База данных (подключение, инициализация)
  3. Клавиатуры
  4. FSM-состояния
  5. Хэндлеры (по разделам)
  6. Точка входа
"""

import asyncio
import logging
import os

import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ─────────────────────────────────────────────────────────────
#  1. КОНФИГУРАЦИЯ
#  Вставь свои значения ниже или задай их как переменные окружения
#  на Render (Settings → Environment → Add Environment Variable)
# ─────────────────────────────────────────────────────────────

# Токен от @BotFather
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_СЮДА")

# URL базы данных PostgreSQL (см. раздел "Где взять бесплатный PostgreSQL" ниже)
# Формат: postgresql://user:password@host:5432/dbname
DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql://user:password@host/dbname")

# Ссылка для поддержки проекта (донаты, Boosty, и т.д.)
SUPPORT_URL: str = "https://boosty.to/ТВОЙ_ПРОФИЛЬ"

# ──────────────────────────────────────────────────────────────
#  File ID шаблонов бинго-карточек.
#  Как получить file_id:
#  1. Запусти бота
#  2. Отправь боту картинку-шаблон как ФАЙЛ (не фото!)
#  3. Добавь временный хэндлер: print(message.document.file_id)
#  4. Скопируй полученный ID сюда и убери хэндлер
# ──────────────────────────────────────────────────────────────
TEMPLATES: dict[str, dict] = {
    "tmpl_1": {
        "label": "🎴 Шаблон «Классик»",
        "file_id": "ВСТАВЬ_FILE_ID_ШАБЛОНА_1",
        "caption": "Классическое бинго 5×5. Впиши свои интересы!",
    },
    "tmpl_2": {
        "label": "🌸 Шаблон «Аниме»",
        "file_id": "ВСТАВЬ_FILE_ID_ШАБЛОНА_2",
        "caption": "Бинго в аниме-стиле. Для истинных вибов!",
    },
    "tmpl_3": {
        "label": "🌙 Шаблон «Тёмный»",
        "file_id": "ВСТАВЬ_FILE_ID_ШАБЛОНА_3",
        "caption": "Тёмное бинго для ночных птиц.",
    },
}


# ─────────────────────────────────────────────────────────────
#  2. БАЗА ДАННЫХ
# ─────────────────────────────────────────────────────────────

async def create_pool() -> asyncpg.Pool:
    """Создаёт пул подключений к PostgreSQL."""
    return await asyncpg.create_pool(DATABASE_URL)


async def init_db(pool: asyncpg.Pool) -> None:
    """
    Создаёт таблицы при первом запуске.
    Безопасно: использует IF NOT EXISTS.
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            -- Таблица пользователей
            CREATE TABLE IF NOT EXISTS users (
                user_id      BIGINT PRIMARY KEY,
                username     TEXT,          -- @username или first_name
                bingo_file_id TEXT,         -- file_id документа на серверах Telegram
                created_at   TIMESTAMPTZ DEFAULT NOW()
            );

            -- Таблица реакций (лайки и скипы в одной таблице)
            -- liked = TRUE  → лайк (вайб)
            -- liked = FALSE → скип
            CREATE TABLE IF NOT EXISTS interactions (
                from_user_id BIGINT NOT NULL,
                to_user_id   BIGINT NOT NULL,
                liked        BOOLEAN NOT NULL,
                created_at   TIMESTAMPTZ DEFAULT NOW(),
                PRIMARY KEY (from_user_id, to_user_id)
            );
        """)


# ─────────────────────────────────────────────────────────────
#  3. КЛАВИАТУРЫ
# ─────────────────────────────────────────────────────────────

def kb_main_menu():
    b = InlineKeyboardBuilder()
    b.button(text="👤 Мой профиль", callback_data="profile")
    b.button(text="🔍 Поиск",       callback_data="search")
    b.button(text="📥 Шаблоны",     callback_data="templates")
    b.button(text="☕ Поддержать",   callback_data="support")
    b.adjust(2, 2)
    return b.as_markup()


def kb_back():
    b = InlineKeyboardBuilder()
    b.button(text="🏠 Главное меню", callback_data="menu")
    return b.as_markup()


def kb_profile(has_bingo: bool):
    b = InlineKeyboardBuilder()
    label = "🔄 Обновить бинго" if has_bingo else "📤 Загрузить бинго"
    b.button(text=label,           callback_data="upload_bingo")
    b.button(text="📥 Шаблоны",   callback_data="templates")
    b.button(text="🏠 Меню",      callback_data="menu")
    b.adjust(1)
    return b.as_markup()


def kb_cancel():
    b = InlineKeyboardBuilder()
    b.button(text="❌ Отмена", callback_data="cancel_upload")
    return b.as_markup()


def kb_search_card(target_user_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="👍 Вайб",  callback_data=f"like:{target_user_id}")
    b.button(text="👎 Скип",  callback_data=f"skip:{target_user_id}")
    b.button(text="🏠 Меню", callback_data="exit_search")
    b.adjust(2, 1)
    return b.as_markup()


def kb_templates():
    b = InlineKeyboardBuilder()
    for key, data in TEMPLATES.items():
        b.button(text=data["label"], callback_data=f"template:{key}")
    b.button(text="🏠 Назад", callback_data="menu")
    b.adjust(1)
    return b.as_markup()


# ─────────────────────────────────────────────────────────────
#  4. FSM-СОСТОЯНИЯ
# ─────────────────────────────────────────────────────────────

class UploadBingo(StatesGroup):
    waiting_for_document = State()


# ─────────────────────────────────────────────────────────────
#  5. ХЭНДЛЕРЫ
# ─────────────────────────────────────────────────────────────

router = Router()


# ── 5.1  /start ──────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, pool: asyncpg.Pool):
    user_id  = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    async with pool.acquire() as conn:
        # Создаём пользователя или обновляем username
        await conn.execute("""
            INSERT INTO users (user_id, username)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username
        """, user_id, username)

    await message.answer(
        "🎱 <b>Добро пожаловать в Bingo Dating!</b>\n\n"
        "Здесь знакомятся по-другому:\n"
        "вместо скучных анкет — <b>бинго-карточки</b> с твоим вайбом, "
        "интересами и чертами характера.\n\n"
        "Загрузи своё бинго → ищи похожих людей → мэтч 🔥",
        reply_markup=kb_main_menu(),
        parse_mode=ParseMode.HTML,
    )


# ── 5.2  Главное меню (возврат) ──────────────────────────────

@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>",
        reply_markup=kb_main_menu(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


# ── 5.3  Профиль ─────────────────────────────────────────────

@router.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery, pool: asyncpg.Pool):
    user_id = callback.from_user.id

    async with pool.acquire() as conn:
        user = await conn.fetchrow(
            "SELECT username, bingo_file_id FROM users WHERE user_id = $1",
            user_id,
        )

    has_bingo = bool(user and user["bingo_file_id"])

    if has_bingo:
        # Показываем текущую карточку
        await callback.message.answer_document(
            document=user["bingo_file_id"],
            caption=(
                f"👤 <b>Твой профиль</b>\n\n"
                f"Ник: @{user['username'] or '—'}\n"
                f"Статус: ✅ Бинго загружено"
            ),
            reply_markup=kb_profile(has_bingo=True),
            parse_mode=ParseMode.HTML,
        )
        await callback.message.delete()
    else:
        await callback.message.edit_text(
            "👤 <b>Мой профиль</b>\n\n"
            "У тебя ещё нет бинго-карточки.\n\n"
            "Скачай шаблон → заполни → загрузи как <b>файл</b> (не фото!).\n"
            "Без карточки поиск недоступен — это честно 😊",
            reply_markup=kb_profile(has_bingo=False),
            parse_mode=ParseMode.HTML,
        )

    await callback.answer()


# ── 5.4  Загрузка бинго ──────────────────────────────────────

@router.callback_query(F.data == "upload_bingo")
async def cb_upload_bingo(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UploadBingo.waiting_for_document)
    await callback.message.edit_text(
        "📤 <b>Загрузка бинго</b>\n\n"
        "Отправь заполненную карточку как <b>файл</b>:\n"
        "📎 → «Файл» → выбери картинку\n\n"
        "⚠️ Не прикрепляй как фото — качество пострадает!",
        reply_markup=kb_cancel(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_upload")
async def cb_cancel_upload(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>",
        reply_markup=kb_main_menu(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer("Отменено")


@router.message(UploadBingo.waiting_for_document, F.document)
async def receive_bingo(message: Message, state: FSMContext, pool: asyncpg.Pool):
    doc = message.document

    # Проверяем MIME-тип — только изображения
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await message.answer(
            "❌ Это не картинка!\n\n"
            "Поддерживаемые форматы: PNG, JPG, WEBP и т.д.\n"
            "Попробуй ещё раз.",
            reply_markup=kb_cancel(),
        )
        return

    user_id  = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE users
            SET bingo_file_id = $1, username = $2
            WHERE user_id = $3
        """, doc.file_id, username, user_id)

    await state.clear()
    await message.answer(
        "✅ <b>Бинго загружено!</b>\n\n"
        "Теперь другие пользователи увидят твою карточку.\n"
        "Удачи в поиске своего вайба! 🎱",
        reply_markup=kb_main_menu(),
        parse_mode=ParseMode.HTML,
    )


@router.message(UploadBingo.waiting_for_document)
async def wrong_file_type(message: Message):
    """Ловим всё, что не документ, пока ждём загрузки."""
    await message.answer(
        "⚠️ Жду файл, а не это.\n\n"
        "Нажми 📎 → «Файл» и выбери своё бинго-изображение.",
        reply_markup=kb_cancel(),
    )


# ── 5.5  Шаблоны ─────────────────────────────────────────────

@router.callback_query(F.data == "templates")
async def cb_templates(callback: CallbackQuery):
    await callback.message.edit_text(
        "📥 <b>Шаблоны бинго</b>\n\n"
        "Выбери шаблон, скачай, заполни в любом редакторе "
        "и загрузи обратно как файл:",
        reply_markup=kb_templates(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("template:"))
async def cb_send_template(callback: CallbackQuery):
    key  = callback.data.split(":", 1)[1]
    tmpl = TEMPLATES.get(key)

    if not tmpl:
        await callback.answer("Неизвестный шаблон", show_alert=True)
        return

    if tmpl["file_id"].startswith("ВСТАВЬ"):
        await callback.answer(
            "⚙️ Этот шаблон ещё не загружен администратором.",
            show_alert=True,
        )
        return

    b = InlineKeyboardBuilder()
    b.button(text="📤 Загрузить моё бинго", callback_data="upload_bingo")
    b.button(text="◀️ Назад к шаблонам",    callback_data="templates")

    await callback.message.answer_document(
        document=tmpl["file_id"],
        caption=(
            f"🎴 <b>{tmpl['label']}</b>\n\n"
            f"{tmpl['caption']}\n\n"
            "Заполни и загрузи обратно как <b>файл</b>!"
        ),
        reply_markup=b.as_markup(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


# ── 5.6  Поиск ───────────────────────────────────────────────

async def fetch_next_profile(pool: asyncpg.Pool, user_id: int):
    """Достаём случайный профиль, которого юзер ещё не видел."""
    async with pool.acquire() as conn:
        return await conn.fetchrow("""
            SELECT u.user_id, u.username, u.bingo_file_id
            FROM   users u
            WHERE  u.user_id != $1
              AND  u.bingo_file_id IS NOT NULL
              AND  u.user_id NOT IN (
                       SELECT to_user_id
                       FROM   interactions
                       WHERE  from_user_id = $1
                   )
            ORDER BY RANDOM()
            LIMIT 1
        """, user_id)


async def send_profile_card(
    target,
    send_fn,          # message.answer_document или bot.send_document
    delete_prev=None, # message.delete() если нужно убрать предыдущее
):
    """Отправляем карточку пользователя."""
    if delete_prev:
        try:
            await delete_prev()
        except Exception:
            pass

    if target["username"]:
        mention = f'<a href="tg://user?id={target["user_id"]}">@{target["username"]}</a>'
    else:
        mention = f'<a href="tg://user?id={target["user_id"]}">Аноним #{target["user_id"] % 10000}</a>'

    await send_fn(
        document=target["bingo_file_id"],
        caption=f"🔍 Бинго от {mention}\n\nЧто думаешь?",
        reply_markup=kb_search_card(target["user_id"]),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "search")
async def cb_search(callback: CallbackQuery, pool: asyncpg.Pool):
    user_id = callback.from_user.id

    # Проверяем, есть ли у самого юзера бинго
    async with pool.acquire() as conn:
        me = await conn.fetchrow(
            "SELECT bingo_file_id FROM users WHERE user_id = $1", user_id
        )

    if not me or not me["bingo_file_id"]:
        await callback.message.edit_text(
            "❗ <b>Сначала загрузи своё бинго!</b>\n\n"
            "Без карточки поиск недоступен — это честно 😊",
            reply_markup=kb_profile(has_bingo=False),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return

    target = await fetch_next_profile(pool, user_id)

    if not target:
        await callback.message.edit_text(
            "🏜️ <b>Пока никого нет...</b>\n\n"
            "Ты просмотрел всех доступных пользователей.\n"
            "Возвращайся позже — база пополняется!",
            reply_markup=kb_back(),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return

    await send_profile_card(
        target,
        send_fn=callback.message.answer_document,
        delete_prev=callback.message.delete,
    )
    await callback.answer()


@router.callback_query(F.data == "exit_search")
async def cb_exit_search(callback: CallbackQuery):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "🏠 <b>Главное меню</b>",
        reply_markup=kb_main_menu(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


# ── 5.7  Лайк / Скип + Мэтч ──────────────────────────────────

async def record_interaction(pool: asyncpg.Pool, from_id: int, to_id: int, liked: bool):
    """Сохраняем реакцию и возвращаем True если это мэтч."""
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO interactions (from_user_id, to_user_id, liked)
            VALUES ($1, $2, $3)
            ON CONFLICT (from_user_id, to_user_id) DO UPDATE SET liked = EXCLUDED.liked
        """, from_id, to_id, liked)

        if not liked:
            return False

        # Проверяем взаимный лайк
        mutual = await conn.fetchval("""
            SELECT 1 FROM interactions
            WHERE from_user_id = $1
              AND to_user_id   = $2
              AND liked        = TRUE
        """, to_id, from_id)

        return bool(mutual)


async def notify_match(bot: Bot, pool: asyncpg.Pool, user_a: int, user_b: int):
    """Отправляем обоим уведомление о мэтче."""
    async with pool.acquire() as conn:
        a = await conn.fetchrow("SELECT username FROM users WHERE user_id = $1", user_a)
        b = await conn.fetchrow("SELECT username FROM users WHERE user_id = $1", user_b)

    def mention(uid, row):
        name = f"@{row['username']}" if row["username"] else f"Аноним #{uid % 10000}"
        return f'<a href="tg://user?id={uid}">{name}</a>'

    msg_a = (
        "🎉 <b>Мэтч!</b>\n\n"
        f"Вы с {mention(user_b, b)} понравились друг другу!\n"
        "Напиши первым — не стесняйся 😊"
    )
    msg_b = (
        "🎉 <b>Мэтч!</b>\n\n"
        f"Вы с {mention(user_a, a)} понравились друг другу!\n"
        "Напиши первым — не стесняйся 😊"
    )

    for uid, text in [(user_a, msg_a), (user_b, msg_b)]:
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.warning(f"Не удалось уведомить {uid}: {e}")


@router.callback_query(F.data.startswith("like:"))
async def cb_like(callback: CallbackQuery, pool: asyncpg.Pool, bot: Bot):
    from_id = callback.from_user.id
    to_id   = int(callback.data.split(":")[1])

    is_match = await record_interaction(pool, from_id, to_id, liked=True)

    if is_match:
        await notify_match(bot, pool, from_id, to_id)

    # Убираем кнопки с текущей карточки
    try:
        old_caption = callback.message.caption or ""
        await callback.message.edit_caption(
            caption=old_caption + "\n\n✅ <i>Вайб отмечен!</i>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    await callback.answer("👍 Вайб!" + (" 🎉 Мэтч!" if is_match else ""))

    # Показываем следующий профиль
    target = await fetch_next_profile(pool, from_id)
    if target:
        await send_profile_card(target, callback.message.answer_document)
    else:
        await callback.message.answer(
            "🏜️ Ты просмотрел всех на сегодня. Возвращайся позже!",
            reply_markup=kb_back(),
        )


@router.callback_query(F.data.startswith("skip:"))
async def cb_skip(callback: CallbackQuery, pool: asyncpg.Pool):
    from_id = callback.from_user.id
    to_id   = int(callback.data.split(":")[1])

    await record_interaction(pool, from_id, to_id, liked=False)

    try:
        old_caption = callback.message.caption or ""
        await callback.message.edit_caption(
            caption=old_caption + "\n\n⏭ <i>Пропущено</i>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass

    await callback.answer("👎 Скип")

    target = await fetch_next_profile(pool, from_id)
    if target:
        await send_profile_card(target, callback.message.answer_document)
    else:
        await callback.message.answer(
            "🏜️ Ты просмотрел всех на сегодня. Возвращайся позже!",
            reply_markup=kb_back(),
        )


# ── 5.8  Поддержка ───────────────────────────────────────────

@router.callback_query(F.data == "support")
async def cb_support(callback: CallbackQuery):
    b = InlineKeyboardBuilder()
    b.button(text="☕ Поддержать проект", url=SUPPORT_URL)
    b.button(text="🏠 Назад",            callback_data="menu")
    b.adjust(1)

    await callback.message.edit_text(
        "☕ <b>Поддержать проект</b>\n\n"
        "Bingo Dating — независимый некоммерческий проект.\n"
        "Если идея нравится — поддержи нас! Это помогает "
        "держать сервер живым и развивать бота 🙏",
        reply_markup=b.as_markup(),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


#

import logging
import json
import base64
import pickle
import asyncio
import random
import threading
import time as _time
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytz
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
    BotCommand, MenuButtonCommands,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
import anthropic
import gspread
from google.oauth2.service_account import Credentials

import os

# ── КОНФИГУРАЦИЯ ─────────────────────────────────────────────────────────────
BOT_TOKEN          = os.environ.get("BOT_TOKEN")
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY")
GOOGLE_CREDENTIALS = os.environ.get("GOOGLE_CREDENTIALS")
SPREADSHEET_ID     = os.environ.get("SPREADSHEET_ID")
TRAINER_ID         = int(os.environ.get("TRAINER_ID", "0"))

_TZ_RAW     = os.environ.get("TZ", "Europe/Moscow").strip()
TIMEZONE    = pytz.timezone(_TZ_RAW)
TZ_FOR_JOBS = ZoneInfo(_TZ_RAW)

CLAUDE_MODEL = "claude-sonnet-4-6"

# На Railway смонтируй Volume на /data и поставь STATE_FILE=/data/bot_states.pkl
STATE_FILE = os.environ.get("STATE_FILE", "bot_states.pkl")

# ── ФАЙЛЫ ЗАЩИТЫ ДАННЫХ ──────────────────────────────────────────────────────
# Кладём рядом со STATE_FILE — то есть на тот же Volume.
_STATE_DIR   = os.path.dirname(STATE_FILE) or "."
HW_FILE      = os.environ.get("HW_FILE",      os.path.join(_STATE_DIR, "progress_hw.json"))
JOURNAL_FILE = os.environ.get("JOURNAL_FILE", os.path.join(_STATE_DIR, "progress_journal.jsonl"))
PENDING_FILE = os.environ.get("PENDING_FILE", os.path.join(_STATE_DIR, "pending_ops.jsonl"))
BACKUP_DIR   = os.environ.get("BACKUP_DIR",   os.path.join(_STATE_DIR, "backups"))

# Минимальный интервал между тяжёлыми ИИ-запросами на одного пользователя (сек)
AI_COOLDOWN_SEC = int(os.environ.get("AI_COOLDOWN_SEC", "15"))

# ── XP / МОНЕТЫ ──────────────────────────────────────────────────────────────
XP_PER_WORKOUT    = 20
XP_PER_FOOD_DAY   = 10
XP_PER_WELLBEING  = 10
XP_SCHEDULE_BONUS = 50
XP_GOALS_BONUS    = 100
XP_ANKETA_BONUS   = 150
XP_WEIGH_IN       = 30
XP_PLAN_DONE      = 50
XP_STREAK_WEEK    = 50

COINS_PER_WORKOUT  = 8
COINS_PER_FOOD_DAY = 3
COINS_ANKETA       = 100
COINS_GOALS        = 30
COINS_SCHEDULE     = 20
COINS_WEIGH_IN     = 20
COINS_PLAN_DONE    = 30
COINS_STREAK_WEEK  = 30

# ── УРОВНИ ───────────────────────────────────────────────────────────────────
LEVELS = {
    0:     "🫡 Протрузианец",
    450:   "⚔️ Адепт",
    1500:  "🏋️ Лифтер",
    4000:  "💎 Титан",
    7500:  "🤖 Киборг",
    12000: "👑 Легенда",
}

LEVEL_UP_MESSAGES = {
    "🫡 Протрузианец": (
        "👋 Ты — Протрузианец.\nНе переживай, все с этого начинали.\n"
        "Сделай первый шаг — и братство тебя заметит.\n"
        "Впереди адепт, лифтер, титан, киборг и легенда."
    ),
    "⚔️ Адепт": (
        "⚔️ *АДЕПТ!*\n\nПротрузианец остался позади. Теперь ты — в деле.\n"
        "Братство чувствует твою энергию. Уважает.\n"
        "Тренировки — не подвиг. Твоя работа.\n"
        "Протрузия? Какая протрузия?\n"
        "Иди дальше. Лифтер уже близко. 🏋️"
    ),
    "🏋️ Лифтер": (
        "💥 *ЛИФТЕР!*\n\nТы не просто тренируешься — ты живёшь этим.\n"
        "Мощь. Напор. Здоровый пофигизм.\n"
        "Штанга здоровается с тобой первой.\n"
        "Следующая остановка: 💎 Титан"
    ),
    "💎 Титан": (
        "💎 *ТИТАН!*\n\nТы — глыба. Непоколебимый.\n"
        "Привычки крепче утреннего кофе.\n"
        "Братство знает: если Титан двинулся — не остановишь.\n"
        "Дальше — 🤖 Киборг"
    ),
    "🤖 Киборг": (
        "⚙️ *КИБОРГ!*\n\nОшибка 418: 'Я не чайник'.\n"
        "Стабильность — режим по умолчанию.\n"
        "Эмоции — по желанию. Тренировка — в любое время.\n"
        "Ты — машина. С душой. Но она спит до отбоя.\n"
        "Остался последний шаг. Легенда ждёт. 👑"
    ),
    "👑 Легенда": (
        "🏆 *ЛЕГЕНДА!*\n\nТы прошёл путь от 'болит' до 'давай ещё'.\n"
        "Ты не бросил, когда было лень, темно или вообще никак.\n"
        "Теперь ты — легенда. Братство склоняет голову.\n"
        "Можешь выдохнуть. Но лучше не останавливайся."
    ),
}

# ── ССЫЛКИ БОНУСНЫХ КАНАЛОВ ──────────────────────────────────────────────────
SUPPLEMENT_SHOP_URL = "https://t.me/Mysterioms_bot?startapp"
MANAGER_URL         = "https://t.me/kokos_vadimovich"

# ── АНКЕТА ───────────────────────────────────────────────────────────────────
ANKETA = [
    ("name",          "text",   "✨ Как мне к тебе обращаться?", None),
    ("age",           "text",   "📅 Сколько тебе лет?", None),
    ("gender",        "choice", "👤 Твой пол?",
     ["👨 Мужской", "👩 Женский", "🌀 Другое"]),
    ("height",        "text",   "📏 Твой рост (см)?", None),
    ("weight",        "text",   "⚖️ Текущий вес (кг)?", None),
    ("target_weight", "text",   "🎯 Желаемый вес (кг)?", None),
    ("health",        "text",   "🩺 Травмы или хронические заболевания? (если нет — напиши «нет»)", None),
    ("nutrition",     "choice", "🥗 Как обычно питаешься?",
     ["🧮 Считаю КБЖУ", "👀 Слежу примерно", "🍔 Ем всё подряд", "🥦 На диете"]),
    ("sleep",         "choice", "😴 Сколько спишь?",
     ["😌 7-8 ч — высыпаюсь", "🙂 6-7 ч — нормально", "🥱 Меньше 6 ч — недосып"]),
    ("stress",        "choice", "🌊 Уровень стресса в жизни?",
     ["🟢 Низкий — всё спокойно", "🟡 Средний — бывает", "🔴 Высокий — постоянно"]),
    ("alcohol",       "choice", "🍷 Как часто употребляешь алкоголь?",
     ["🚫 Не пью совсем", "🥂 По праздникам", "🍺 1-2 раза в неделю"]),
    ("activity",      "choice", "🚶 Активность вне зала?",
     ["🪑 Сижу весь день", "🚶 Хожу пешком", "🔨 Физический труд"]),
    ("experience",    "choice", "🏋️ Опыт тренировок?",
     ["🌱 Новичок (0-3 мес)", "💪 Любитель (3-12 мес)", "🔥 Продвинутый (1-3 года)", "🏆 Спортсмен (3+ лет)"]),
    ("motivation",    "text",   "💫 Что тебя мотивирует тренироваться?", None),
    ("psych",         "choice", "🧠 Что тебя больше всего заряжает в тренировках?",
     ["🏆 Рекорды и рост",
      "👥 Братство и поддержка",
      "📊 Цифры и прогресс",
      "💪 Самочувствие и сила",
      "🎯 Конкретная цель"]),
    ("extra",         "text",   "📝 Что ещё важно знать о тебе? (если нет — напиши «нет»)", None),
]
ANKETA_KEYS = [q[0] for q in ANKETA]

# ── РЕКОРДЫ ──────────────────────────────────────────────────────────────────
RECORDS_EXERCISES = [
    ("squat",    "🏋️ Приседания со штангой — рабочий максимум (кг)?"),
    ("bench",    "💪 Жим лёжа — максимум (кг)?"),
    ("deadlift", "🔥 Становая тяга — максимум (кг)?"),
    ("pullup",   "🧗 Подтягивания с весом — доп. вес (кг, если без веса — 0)?"),
]
RECORDS_KEYS = [e[0] for e in RECORDS_EXERCISES]
RECORDS_SEASON_DAYS = 90

# ── РАСПИСАНИЕ ───────────────────────────────────────────────────────────────
SCHEDULE_QUESTIONS = [
    ("Сколько раз в неделю готов тренироваться?",
     ["2 раза в неделю", "3 раза в неделю", "4 раза в неделю", "5 и более раз"]),
    ("В какие дни предпочитаешь?",
     ["Пн, Ср, Пт", "Вт, Чт, Сб", "Пн, Ср, Пт, Вс", "Гибко — как получится"]),
    ("В какое время удобнее всего?",
     ["Утро (7:00 – 10:00)", "День (12:00 – 15:00)", "Вечер (18:00 – 20:00)", "Поздно (20:00 – 22:00)"]),
    ("Сколько времени готов уделять одной тренировке?",
     ["30 минут", "45 минут", "60 минут", "90 минут"]),
]

# ── ТЕСТ ЦЕЛЕЙ ───────────────────────────────────────────────────────────────
GOALS_TEST = [
    {"q": "🎯 Что хочешь прокачать в первую очередь?",
     "options": ["🏋️ Сила — жать и тянуть больше", "🏃 Выносливость — не задыхаться",
                 "⚡ Скорость и взрывная мощь", "🤸 Гибкость — чтобы тело не скрипело",
                 "✨ Рельеф — чтобы видно было", "🔥 Всё сразу — общая форма"]},
    {"q": "🥈 А что на втором месте?",
     "options": ["🏋️ Сила — жать и тянуть больше", "🏃 Выносливость — не задыхаться",
                 "⚡ Скорость и взрывная мощь", "🤸 Гибкость — чтобы тело не скрипело",
                 "✨ Рельеф — чтобы видно было", "🔥 Всё сразу — общая форма"]},
    {"q": "🏋️ Какой формат тренировок тебе заходит?",
     "options": ["🏋️ Железо — штанга, гантели", "🚴 Кардио — бег, велосипед",
                 "🤾 Функционалка — всё тело", "🥊 Единоборства",
                 "🧘 Йога / растяжка", "🔥 Всего понемногу"]},
    {"q": "📈 Через 3 месяца — что хочешь увидеть?",
     "options": ["💪 Рабочие веса выросли", "🏃 Бегу 5 км без остановки",
                 "✨ Смотрю в зеркало — и нравится", "🌟 Энергия на всё хватает",
                 "⚖️ Весы показывают нужную цифру", "🏆 Просто быть в лучшей форме в жизни"]},
    {"q": "🚧 Что обычно мешает дойти до результата?",
     "options": ["😴 Лень и дисциплина", "🗺️ Нет чёткого плана",
                 "🎢 Начинаю — бросаю — по кругу", "🔋 После работы сил ноль",
                 "🤕 Травмы или боли", "✅ Ничего — я готов пахать"]},
    {"q": "⏰ Есть дедлайн — к какой дате нужен результат?",
     "options": ["🔥 Через месяц — горит", "📅 Через 3 месяца",
                 "🗓️ Через полгода", "♾️ Без дедлайна — в своём темпе"]},
    {"q": "💬 Зачем тебе это на самом деле?",
     "options": ["🪞 Хочу нравиться себе в зеркале", "❤️ Здоровье — жить долго и активно",
                 "⚡ Энергия — а не ходить варёным", "💚 Уверенность в себе",
                 "🏅 Спортивные цели и рекорды", "👨‍👩‍👧 Быть примером для своих"]},
    {"q": "🤝 Как тебе комфортнее работать с тренером?",
     "options": ["🔥 Жёстко — пинай и контролируй", "🤗 Мягко — поддерживай и направляй",
                 "📋 Дай план — сам сделаю", "🌀 По ситуации — гибко"]},
]

# ── САМОЧУВСТВИЕ ─────────────────────────────────────────────────────────────
WELLBEING_SURVEY = [
    {"q": "😴 Как ты выспался сегодня?",
     "options": ["💤 Отлично", "🙂 Нормально", "🥱 Так себе", "😩 Не выспался"]},
    {"q": "⚡ Уровень энергии перед тренировкой?",
     "options": ["🔋 Полный заряд", "😌 Норм", "🪫 Низковато", "😮‍💨 На нуле"]},
    {"q": "💪 Мышцы после прошлой тренировки?",
     "options": ["✅ Восстановились", "😐 Лёгкая крепатура", "😣 Сильно болят"]},
]

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── НАДЁЖНАЯ ОТПРАВКА (Markdown-fallback) ─────────────────────────────────────
# Текст от Claude иногда содержит «битый» Markdown.
# При ошибке повторно отправляем без parse_mode — человек гарантированно
# получает контент, пусть и без форматирования.
async def safe_send(context, chat_id, text, reply_markup=None, parse_mode="Markdown"):
    """Отправка с Markdown-fallback и авто-разбивкой длинных сообщений."""
    MAX_LEN = 4000  # Telegram лимит 4096, оставляем запас

    if len(text) <= MAX_LEN:
        try:
            return await context.bot.send_message(
                chat_id=chat_id, text=text,
                parse_mode=parse_mode, reply_markup=reply_markup,
            )
        except Exception:
            try:
                return await context.bot.send_message(
                    chat_id=chat_id, text=text, reply_markup=reply_markup,
                )
            except Exception as e:
                logger.error(f"safe_send failed for {chat_id}: {e}")
                return None

    # Длинное сообщение — разбиваем на части
    parts = []
    while text:
        if len(text) <= MAX_LEN:
            parts.append(text)
            break
        # Ищем перенос строки для чистого разрыва
        cut = text.rfind("\n", 0, MAX_LEN)
        if cut < MAX_LEN // 2:
            cut = MAX_LEN
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")

    last_msg = None
    for i, part in enumerate(parts):
        rm = reply_markup if i == len(parts) - 1 else None
        try:
            last_msg = await context.bot.send_message(
                chat_id=chat_id, text=part,
                parse_mode=parse_mode, reply_markup=rm,
            )
        except Exception:
            try:
                last_msg = await context.bot.send_message(
                    chat_id=chat_id, text=part, reply_markup=rm,
                )
            except Exception as e:
                logger.error(f"safe_send part {i} failed for {chat_id}: {e}")
    return last_msg


async def safe_reply(update, text, reply_markup=None, parse_mode="Markdown"):
    """Ответ с Markdown-fallback и авто-разбивкой длинных сообщений."""
    MAX_LEN = 4000

    if len(text) <= MAX_LEN:
        try:
            return await update.message.reply_text(
                text, parse_mode=parse_mode, reply_markup=reply_markup,
            )
        except Exception:
            try:
                return await update.message.reply_text(text, reply_markup=reply_markup)
            except Exception as e:
                logger.error(f"safe_reply failed: {e}")
                return None

    # Разбиваем на части
    parts = []
    while text:
        if len(text) <= MAX_LEN:
            parts.append(text)
            break
        cut = text.rfind("\n", 0, MAX_LEN)
        if cut < MAX_LEN // 2:
            cut = MAX_LEN
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")

    last_msg = None
    for i, part in enumerate(parts):
        rm = reply_markup if i == len(parts) - 1 else None
        try:
            last_msg = await update.message.reply_text(
                part, parse_mode=parse_mode, reply_markup=rm,
            )
        except Exception:
            try:
                last_msg = await update.message.reply_text(part, reply_markup=rm)
            except Exception as e:
                logger.error(f"safe_reply part {i} failed: {e}")
    return last_msg


# ── PER-USER ЛОКИ — защита XP от гонок ──────────────────────────────────────
# Любой read-modify-write прогресса сериализуется локом пользователя.
# Два события (тренировка + мгновенный бонус) не перезатрут друг друга.
_user_locks: dict[int, asyncio.Lock] = {}

def _user_lock(uid) -> asyncio.Lock:
    uid = int(uid) if not isinstance(uid, int) else uid
    if uid not in _user_locks:
        _user_locks[uid] = asyncio.Lock()
    return _user_locks[uid]


# ── RATE-LIMIT ИИ ─────────────────────────────────────────────────────────────
# Защита от спама дорогими Claude-вызовами.
_ai_last_call: dict[int, float] = {}

def _ai_rate_ok(user_id: int):
    """Возвращает (можно_ли: bool, ждать_сек: int)."""
    now     = _time.monotonic()
    elapsed = now - _ai_last_call.get(user_id, 0)
    if elapsed < AI_COOLDOWN_SEC:
        return False, int(AI_COOLDOWN_SEC - elapsed) + 1
    return True, 0

def _ai_mark(user_id: int):
    _ai_last_call[user_id] = _time.monotonic()


# ── СОСТОЯНИЯ ────────────────────────────────────────────────────────────────
user_states: dict[int, dict]       = {}
anketa_states: dict[int, dict]     = {}
food_states: dict[int, dict]       = {}
goals_test_states: dict[int, dict] = {}
schedule_states: dict[int, dict]   = {}
records_states: dict[int, dict]    = {}
weighin_states: dict[int, dict]    = {}
_processing: set[int]             = set()  # защита от двойного нажатия
wellbeing_states: dict[int, dict]  = {}
cycle_states: dict[int, dict]      = {}
# Локальная отметка «опрос самочувствия сдан сегодня»: {user_id: "17.08.2026"}
# Нужна, чтобы гейт перед отчётом о тренировке НЕ зависел от доступности
# Google Sheets и от задержки кэша.
wellbeing_done_marks: dict[int, str] = {}

_STATE_DICTS = {
    "user_states": user_states, "anketa_states": anketa_states,
    "food_states": food_states, "goals_test_states": goals_test_states,
    "schedule_states": schedule_states, "records_states": records_states,
    "weighin_states": weighin_states, "wellbeing_states": wellbeing_states,
    "cycle_states": cycle_states, "wellbeing_done_marks": wellbeing_done_marks,
}


def save_states():
    try:
        snapshot = {}
        for name, d in _STATE_DICTS.items():
            if name == "food_states":
                snapshot[name] = {k: {**v, "photos": []} for k, v in d.items()}
            else:
                snapshot[name] = dict(d)
        d_dir = os.path.dirname(STATE_FILE)
        if d_dir:
            os.makedirs(d_dir, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(snapshot, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logger.error(f"save_states error: {e}")


def load_states():
    try:
        if not os.path.exists(STATE_FILE):
            return
        with open(STATE_FILE, "rb") as f:
            snapshot = pickle.load(f)
        for name, d in _STATE_DICTS.items():
            saved = snapshot.get(name)
            if isinstance(saved, dict):
                d.clear()
                d.update(saved)
        logger.info("States restored from disk")
    except Exception as e:
        logger.error(f"load_states error: {e}")


# ── КНОПКИ МЕНЮ ──────────────────────────────────────────────────────────────
BTN_STATS         = "📊 Моя статистика"
BTN_WORKOUT       = "🏋️‍♂️ Тренировки\n(+20 XP +8 🪙)"
BTN_FOOD          = "🥗 Питание\n(+10 XP +3 🪙)"
BTN_RECORDS       = "🏆 Рекорды\n(1 кг = 1 XP)"
BTN_TOP           = "💪 ТОП 100 БРАТСТВА 💪"
BTN_BONUS         = "🎁 Подогрев для СВОИХ"
BTN_WEIGHIN       = "⚖️ Измерить вес (Вс)\n(+30 XP +20 🪙)"
BTN_SCHEDULE_NEW  = "📅 Расписание (+50 XP +20 🪙)"
BTN_SCHEDULE_DONE = "📅 Расписание"
BTN_GOALS_NEW     = "🎯 Цели (+100 XP +30 🪙)"
BTN_GOALS_DONE    = "🎯 Цели"
BTN_ANKETA        = "📋 Анкета (+150 XP +100 🪙)"
BTN_CANCEL        = "❌ Отмена"
BTN_CLIENTS       = "👥 Клиенты"
BTN_WEEK          = "📈 Итоги недели"
BTN_DASHBOARD     = "📊 Дашборд"


def main_keyboard(is_trainer=False, anketa_filled=False,
                  schedule_filled=False, goals_filled=False):
    schedule_btn = BTN_SCHEDULE_DONE if schedule_filled else BTN_SCHEDULE_NEW
    goals_btn    = BTN_GOALS_DONE    if goals_filled    else BTN_GOALS_NEW
    rows = [
        [KeyboardButton(BTN_TOP)],
        [KeyboardButton(BTN_BONUS)],
        [KeyboardButton(BTN_STATS), KeyboardButton(schedule_btn)],
        [KeyboardButton(BTN_WORKOUT), KeyboardButton(BTN_FOOD)],
        [KeyboardButton(BTN_RECORDS), KeyboardButton(goals_btn)],
        [KeyboardButton(BTN_WEIGHIN)],
    ]
    if not anketa_filled:
        rows.append([KeyboardButton(BTN_ANKETA)])
    if is_trainer:
        rows.append([KeyboardButton(BTN_CLIENTS), KeyboardButton(BTN_WEEK)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def menu_for(user_id):
    return main_keyboard(
        is_trainer=(user_id == TRAINER_ID),
        anketa_filled=is_anketa_filled(user_id),
        schedule_filled=is_schedule_filled(user_id),
        goals_filled=is_goals_filled(user_id),
    )


def cancel_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton(BTN_CANCEL)]], resize_keyboard=True)


def wide_keyboard(options, prefix):
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(opt, callback_data=f"{prefix}_{i}")]
         for i, opt in enumerate(options)]
    )


# ── MARKDOWN-SAFE ─────────────────────────────────────────────────────────────
def md_safe(text):
    if text is None:
        return ""
    return (str(text)
            .replace("\\", "\\\\")
            .replace("_", "\\_")
            .replace("*", "\\*")
            .replace("[", "\\[")
            .replace("`", "\\`"))


# ── КЕШ ──────────────────────────────────────────────────────────────────────
_CACHE: dict = {}
_SHEETS_SEM = asyncio.Semaphore(8)  # макс 8 параллельных запросов к Sheets
# TTL по типу ключа — профили/анкеты живут дольше, рабочие листы — короче
_TTL_PROGRESS   = 90    # прогресс: часто читается, редко меняется
_TTL_CLIENT_ROW = 150   # строка клиента
_TTL_SHEET      = 45    # таблицы по умолчанию
_TTL_ADMIN_READ = 30    # для тренерских запросов — свежесть важна

_TTL_MAP = {
    "prog_":            90,
    "clirow_":          150,
    "warmup_imp_":      300,
    "sheet_anketa":     200,
    "sheet_clients":    90,
    "sheet_progress":   70,
    "sheet_workouts":   40,
    "sheet_food_log":   40,
    "sheet_wellbeing":  40,
    "sheet_records":    150,
    "sheet_weight_log": 150,
    "sheet_goals":      200,
}

def _ttl_for(key: str) -> int:
    for prefix, ttl in _TTL_MAP.items():
        if key.startswith(prefix):
            return ttl
    return _TTL_SHEET


def _c_get(key, ttl=None):
    e = _CACHE.get(key)
    if e:
        effective_ttl = ttl if ttl is not None else _ttl_for(key)
        if _time.monotonic() - e[1] < effective_ttl:
            return e[0]
    return None


def _c_set(key, value):
    _CACHE[key] = (value, _time.monotonic())


def _c_del(*keys):
    for k in keys:
        _CACHE.pop(k, None)


def _c_del_user(user_id):
    uid = str(user_id)
    for k in list(_CACHE.keys()):
        if uid in k:
            del _CACHE[k]


# ── ТРЕКЕР ДНЕВНЫХ ПУШЕЙ ─────────────────────────────────────────────────────
# Не более 3 пушей в день на одного пользователя.
# Структура: {user_id: {"date": "2024-06-27", "count": 2, "types": {"workout", "food"}}}
# Сбрасывается автоматически при смене даты.
_push_log: dict[int, dict] = {}

def _user_push_limit(user_id: int) -> int:
    """Определяет максимум пушей в день.
    Минимум 2 — утро и вечер приходят всегда.
    Активные → 2 пуша (утро + вечер).
    Умеренные (был вчера) → 2 пуша.
    Неактивные 2+ дней → 3 пуша (утро + день + вечер)."""
    try:
        p, _ = get_progress(user_id)
        today = datetime.now(TIMEZONE).date()

        def days_since(s):
            if not s:
                return 999
            try:
                return (today - datetime.strptime(s, "%Y-%m-%d").date()).days
            except Exception:
                return 999

        dw = days_since(p.get("last_workout", ""))
        df = days_since(p.get("last_food",    ""))
        best = min(dw, df)

        if best == 0:   return 2   # всё сдано — утро + вечер
        if best <= 1:   return 2   # вчера был — утро + вечер
        return 3                   # 2+ дней тишины — утро + день + вечер
    except Exception:
        return 2

def _push_allowed(user_id: int, push_type: str) -> bool:
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    entry = _push_log.get(user_id)
    if not entry or entry.get("date") != today:
        _push_log[user_id] = {"date": today, "count": 0, "types": set()}
        entry = _push_log[user_id]
    limit = _user_push_limit(user_id)
    if entry["count"] >= limit:
        return False
    if push_type in entry["types"]:
        return False
    return True

def _push_mark(user_id: int, push_type: str):
    today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
    if user_id not in _push_log or _push_log[user_id].get("date") != today:
        _push_log[user_id] = {"date": today, "count": 0, "types": set()}
    _push_log[user_id]["count"]  += 1
    _push_log[user_id]["types"].add(push_type)

async def _safe_push(context, user_id: int, push_type: str, text: str,
                     reply_markup=None) -> bool:
    """Отправляет пуш с учётом персонального лимита.
    Активные — 1/день. Умеренные — 2/день. Тихие — 3/день."""
    if not _push_allowed(user_id, push_type):
        return False
    try:
        await safe_send(context, user_id, text, reply_markup=reply_markup)
        _push_mark(user_id, push_type)
        return True
    except Exception as e:
        logger.error(f"_safe_push error {user_id} [{push_type}]: {e}")
        return False

async def _notify_trainer(context, user_id: int, name: str, reason: str):
    """Уведомляет тренера о клиенте — для оперативной реакции."""
    try:
        cl    = _get_client_row(user_id)
        uname = cl[2] if cl and len(cl) > 2 else ""
        uname_part = f" ({md_safe(uname)})" if uname else ""
        await safe_send(context, TRAINER_ID,
                        f"🔔 *{md_safe(name)}*{uname_part}\n"
                        f"`{user_id}`\n\n"
                        f"{reason}")
    except Exception as e:
        logger.error(f"_notify_trainer error: {e}")
_spreadsheet = None


def get_sheet():
    global _spreadsheet
    if _spreadsheet:
        return _spreadsheet
    creds = Credentials.from_service_account_info(
        json.loads(GOOGLE_CREDENTIALS),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    _spreadsheet = gspread.authorize(creds).open_by_key(SPREADSHEET_ID)
    return _spreadsheet


# ═════════════════════════════════════════════════════════════════════════════
# СЛОЙ НАДЁЖНОСТИ GOOGLE SHEETS
# Раньше любая сетевая икота / 429 Rate Limit приводила к тому, что чтение
# «молча» возвращало пустоту — а следом код записывал нули поверх реальных
# данных. Теперь: ретраи с backoff + честное исключение вместо пустоты.
# ═════════════════════════════════════════════════════════════════════════════
class SheetUnavailable(Exception):
    """Google Sheets временно недоступен. НИКОГДА не трактуем как «данных нет»."""


# Глобальный лок на запись в таблицу: read-modify-write по индексу строки
# (col_values -> update) не должен пересекаться между пользователями.
_SHEET_WRITE_LOCK = threading.RLock()


def _gs_call(fn, *args, tries: int = 4, base_delay: float = 0.8, **kwargs):
    """Выполняет вызов gspread с ретраями. Бросает SheetUnavailable, если не вышло."""
    last = None
    for attempt in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            msg = str(e).lower()
            fatal = ("permission" in msg or "not found" in msg
                     or "invalid" in msg and "range" in msg)
            if fatal and attempt == 0:
                # Нет смысла долбить — но всё равно не возвращаем «пусто»
                break
            if attempt < tries - 1:
                _time.sleep(base_delay * (2 ** attempt) + random.uniform(0, 0.4))
    logger.error(f"gspread failed after {tries} tries: {last}")
    raise SheetUnavailable(str(last))


def ws(name):
    """Лист таблицы. Создаём новый ТОЛЬКО если лист реально отсутствует.
    Раньше любая ошибка сети приводила к попытке создать лист заново."""
    try:
        return get_sheet().worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        try:
            return get_sheet().add_worksheet(title=name, rows=1000, cols=20)
        except Exception as e:
            raise SheetUnavailable(f"add_worksheet {name}: {e}")
    except Exception:
        # Сетевая ошибка / 429 — повторяем, но лист НЕ пересоздаём
        try:
            return _gs_call(get_sheet().worksheet, name, tries=3)
        except SheetUnavailable:
            raise
        except gspread.exceptions.WorksheetNotFound:
            return get_sheet().add_worksheet(title=name, rows=1000, cols=20)


# Листы, пустое чтение которых почти наверняка означает сбой, а не «нет данных».
_CRITICAL_SHEETS = {"progress", "clients"}


def _ws_rows(name: str, ttl: int = _TTL_SHEET):
    """Читает лист с кэшем и ретраями.
    ВАЖНО: при сбое бросает SheetUnavailable и НЕ кэширует пустоту."""
    key = f"sheet_{name}"
    cached = _c_get(key, ttl)
    if cached is not None:
        return cached
    rows = _gs_call(ws(name).get_all_values)
    # Защита от «призрачной пустоты»: если критичный лист вдруг пуст,
    # а раньше в нём были данные — это сбой API, а не удаление.
    if name in _CRITICAL_SHEETS and len(rows) <= 1:
        prev = _CACHE.get(key)
        if prev and len(prev[0]) > 1:
            raise SheetUnavailable(f"{name}: подозрительно пустой ответ API")
    _c_set(key, rows)
    return rows


def _ws_rows_safe(name: str, ttl: int = _TTL_SHEET, default=None):
    """Мягкий вариант для read-only мест (пуши, статистика, дашборды),
    где падение задачи хуже, чем неполные данные. Для ЗАПИСИ не использовать!"""
    try:
        return _ws_rows(name, ttl)
    except Exception as e:
        logger.error(f"_ws_rows_safe {name}: {e}")
        return default if default is not None else []


def _ws_invalidate(name: str):
    _c_del(f"sheet_{name}")


SHEET_HEADERS = {
    "clients":    ["ID", "Имя", "Username", "Дата рег", "Частота", "Дни",
                   "Время/длит", "Анкета", "Тренировок", "XP", "Цели",
                   "Расписание", "Дата рекордов"],
    "progress":   ["ID", "Username", "Имя", "Стрик", "Макс стрик", "XP", "Уровень",
                   "Монеты", "Посл. трен", "Посл. еда", "Стрик еды",
                   "Макс стрик еды", "Посл. w-бонус", "Посл. f-бонус"],
    "workouts":   ["Дата", "ID", "Username", "Оценка", "Силовой лог"],
    "food_log":   ["ID", "Username", "Дата", "Приёмы пищи", "КБЖУ-резюме"],
    "weight_log": ["ID", "Дата", "Вес", "Заметка"],
    "goals":      ["ID", "Дата", "Q0", "Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"],
    "anketa":     ["ID", "Дата"] + ANKETA_KEYS,
    "plan_bonus": ["ID", "Неделя"],
    "records":    ["ID", "Дата", "Присед", "Жим", "Становая", "Подтяг", "Сумма"],
    "wellbeing":  ["ID", "Дата", "Сон", "Энергия", "Мышцы"],
}


def clear_sheet_keep_headers(name: str):
    try:
        sheet = ws(name)
        sheet.clear()
        headers = SHEET_HEADERS.get(name)
        if headers:
            sheet.update("A1", [headers])
        _ws_invalidate(name)
    except Exception as e:
        logger.error(f"clear_sheet_keep_headers {name}: {e}")


def ensure_headers():
    """При старте гарантирует строку-заголовок у всех листов.
    Идемпотентно: если шапка уже на месте — ничего не делает."""
    for name, headers in SHEET_HEADERS.items():
        try:
            sheet = ws(name)
            first = sheet.row_values(1)
            if not first or not first[0]:
                sheet.update("A1", [headers])
                _ws_invalidate(name)
            elif first[0].strip() != headers[0]:
                sheet.insert_row(headers, 1)
                _ws_invalidate(name)
        except Exception as e:
            logger.error(f"ensure_headers {name}: {e}")


def uname_of(user):
    return f"@{user.username}" if user.username else str(user.id)


# ── DISPLAY NAME ─────────────────────────────────────────────────────────────
def display_name_for(client_row):
    uid   = client_row[0] if client_row else ""
    uname = client_row[2].strip() if len(client_row) > 2 and client_row[2] else ""
    if uid:
        try:
            rows = _ws_rows("anketa", _TTL_SHEET)
            row  = next((r for r in rows if r and r[0] == str(uid)), None)
            if row and len(row) > 2 and row[2].strip():
                return row[2].strip()
        except Exception:
            pass
    if uname:
        return uname.lstrip("@")
    if uid:
        return f"ID_{str(uid)[:6]}"
    return "Боец"


# ── КЛИЕНТЫ ──────────────────────────────────────────────────────────────────
def _get_client_row(user_id):
    key = f"clirow_{user_id}"
    cached = _c_get(key, _TTL_CLIENT_ROW)
    if cached is not None:
        return cached
    try:
        rows = _ws_rows("clients", _TTL_CLIENT_ROW)
        row  = next((r for r in rows if r and r[0] == str(user_id)), None)
        _c_set(key, row)
        return row
    except Exception:
        return None


def _invalidate_client_row(user_id):
    _c_del(f"clirow_{user_id}", "sheet_clients")


def register_client(user):
    sheet = ws("clients")
    ids = sheet.col_values(1)
    if str(user.id) not in ids:
        sheet.append_row([
            str(user.id), user.first_name or "", uname_of(user),
            datetime.now(TIMEZONE).strftime("%d.%m.%Y"),
            "", "", "", "нет", "0", "0", "нет", "нет", "",
        ])
        _invalidate_client_row(user.id)


def is_anketa_filled(user_id):
    row = _get_client_row(user_id)
    return bool(row and len(row) > 7 and row[7] == "да")


def mark_anketa_filled(user_id):
    try:
        sheet = ws("clients")
        ids   = sheet.col_values(1)
        if str(user_id) in ids:
            sheet.update_cell(ids.index(str(user_id)) + 1, 8, "да")
            _invalidate_client_row(user_id)
    except Exception as e:
        logger.error(f"mark_anketa_filled: {e}")


def mark_goals_filled(user_id):
    try:
        sheet = ws("clients")
        ids   = sheet.col_values(1)
        if str(user_id) in ids:
            sheet.update_cell(ids.index(str(user_id)) + 1, 11, "да")
            _invalidate_client_row(user_id)
    except Exception as e:
        logger.error(f"mark_goals_filled: {e}")


def is_goals_filled(user_id):
    row = _get_client_row(user_id)
    return bool(row and len(row) > 10 and row[10] == "да")


def mark_schedule_filled(user_id):
    try:
        sheet = ws("clients")
        ids   = sheet.col_values(1)
        if str(user_id) in ids:
            sheet.update_cell(ids.index(str(user_id)) + 1, 12, "да")
            _invalidate_client_row(user_id)
    except Exception as e:
        logger.error(f"mark_schedule_filled: {e}")


def is_schedule_filled(user_id):
    row = _get_client_row(user_id)
    return bool(row and len(row) > 11 and row[11] == "да")


def get_records_last_date(user_id):
    row = _get_client_row(user_id)
    try:
        if row and len(row) > 12 and row[12]:
            return datetime.strptime(row[12], "%d.%m.%Y").date()
    except Exception:
        pass
    return None


def mark_records_date(user_id):
    try:
        sheet = ws("clients")
        ids   = sheet.col_values(1)
        if str(user_id) in ids:
            sheet.update_cell(ids.index(str(user_id)) + 1, 13,
                              datetime.now(TIMEZONE).strftime("%d.%m.%Y"))
            _invalidate_client_row(user_id)
    except Exception as e:
        logger.error(f"mark_records_date: {e}")


def get_all_clients():
    try:
        rows  = _ws_rows("clients", _TTL_CLIENT_ROW)
        if not rows:
            return []
        first = rows[0]
        if first and first[0] and first[0].isdigit():
            return rows
        return rows[1:]
    except Exception:
        return []


def update_client_stats(user_id, field, value):
    """Обновляет счётчик тренировок или XP в листе clients.
    Батчинг: читаем обе ячейки за один запрос, пишем за один."""
    try:
        with _SHEET_WRITE_LOCK:
            sheet = ws("clients")
            ids   = _gs_call(sheet.col_values, 1)
            if str(user_id) not in ids:
                return
            idx = ids.index(str(user_id)) + 1
            # Читаем обе ячейки одним запросом
            vals = _gs_call(sheet.get, f"I{idx}:J{idx}")

            def _num(v):
                try:
                    return int(str(v).strip())
                except Exception:
                    return None

            cur_w = _num(vals[0][0]) if vals and vals[0] and len(vals[0]) > 0 else 0
            cur_x = _num(vals[0][1]) if vals and vals[0] and len(vals[0]) > 1 else 0
            # Пустая ячейка = 0, а вот нечитаемая — повод не трогать счётчики
            cur_w = 0 if cur_w is None and not (vals and vals[0] and vals[0][0]) else cur_w
            cur_x = 0 if cur_x is None and not (vals and vals[0] and len(vals[0]) > 1 and vals[0][1]) else cur_x
            if cur_w is None or cur_x is None:
                logger.error(f"update_client_stats: нечитаемые счётчики {user_id}, пропуск")
                return
            # Счётчики в clients — витрина; они не могут уменьшаться
            new_w = cur_w + value if field == "workouts" else cur_w
            new_x = cur_x + value if field == "xp"       else cur_x
            _gs_call(sheet.update, f"I{idx}:J{idx}",
                     [[max(new_w, cur_w), max(new_x, cur_x)]])
            _invalidate_client_row(user_id)
    except Exception as e:
        logger.error(f"update_client_stats {user_id}: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# ЗАЩИТА ПРОГРЕССА: HIGH-WATER MARKS + ЖУРНАЛ + ОЧЕРЕДЬ ПОВТОРА
#
# Причина потери прогресса подопечного:
#   1. get_progress() при ЛЮБОЙ ошибке чтения таблицы молча возвращал нули
#      и ещё и кэшировал их на 90 секунд.
#   2. Все функции записи (XP, тренировка, питание, бонусы) начинаются
#      с get_progress() и пишут обратно ВСЮ строку целиком.
#   → Одна ошибка 429/500 от Google API = XP, монеты, стрик и ранг
#     перезаписаны нулями. Навсегда, без следа в логах.
#
# Теперь действуют три независимых рубежа:
#   • Рубеж 1 — чтение не может «притвориться нулями»: сбой = исключение.
#   • Рубеж 2 — HW-марки: локальный несгораемый максимум XP/монет/рекордов.
#     Даже если таблица отдаст мусор, запись ниже максимума невозможна.
#   • Рубеж 3 — журнал каждой записи на диск + очередь повтора неудачных
#     начислений. Ничего не теряется, всё восстановимо.
# ═════════════════════════════════════════════════════════════════════════════
PROGRESS_EMPTY = {
    "streak": 0, "max_streak": 0, "xp": 0, "level": 0, "coins": 0,
    "last_workout": "", "last_food": "", "food_streak": 0,
    "food_max_streak": 0, "last_wbonus": "", "last_fbonus": "",
}

# Поля, которые физически не могут уменьшаться в этой системе
_MONOTONIC_FIELDS = ("xp", "coins", "max_streak", "food_max_streak")

_HW: dict = {}                 # {uid_str: {...}}
_HW_LOCK = threading.RLock()
_HW_DIRTY = False
_INTEGRITY_ALERTS: list = []   # накопитель для уведомления тренера


def _atomic_write(path: str, data: str):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)


def hw_load():
    """Поднимает несгораемые максимумы с диска при старте."""
    global _HW
    try:
        if os.path.exists(HW_FILE):
            with open(HW_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _HW = {str(k): v for k, v in data.items() if isinstance(v, dict)}
            logger.info(f"HW-марки загружены: {len(_HW)} пользователей")
    except Exception as e:
        logger.error(f"hw_load error: {e}")


def hw_save(force: bool = False):
    global _HW_DIRTY
    with _HW_LOCK:
        if not _HW_DIRTY and not force:
            return
        try:
            _atomic_write(HW_FILE, json.dumps(_HW, ensure_ascii=False))
            _HW_DIRTY = False
        except Exception as e:
            logger.error(f"hw_save error: {e}")


def hw_get(user_id) -> dict:
    with _HW_LOCK:
        return dict(_HW.get(str(user_id), {}))


def hw_update(user_id, p: dict):
    """Обновляет несгораемые максимумы. Только вверх — никогда вниз."""
    global _HW_DIRTY
    uid = str(user_id)
    with _HW_LOCK:
        cur = _HW.setdefault(uid, {})
        changed = False
        for f in _MONOTONIC_FIELDS:
            v = int(p.get(f, 0) or 0)
            if v > int(cur.get(f, 0) or 0):
                cur[f] = v
                changed = True
        # Оперативные поля храним «как есть» — они нужны для восстановления строки
        for f in ("streak", "level", "food_streak"):
            v = int(p.get(f, 0) or 0)
            if cur.get(f) != v:
                cur[f] = v
                changed = True
        for f in ("last_workout", "last_food", "last_wbonus", "last_fbonus"):
            v = p.get(f, "") or ""
            if v and cur.get(f) != v:
                cur[f] = v
                changed = True
        if changed:
            cur["ts"] = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
            _HW_DIRTY = True


def _journal(event: str, user_id, payload: dict):
    """Пишет каждое изменение прогресса в JSONL на диск. Это чёрный ящик:
    после любого инцидента видно, что и когда изменилось."""
    try:
        line = json.dumps({
            "ts":   datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
            "ev":   event,
            "uid":  str(user_id),
            **payload,
        }, ensure_ascii=False)
        d = os.path.dirname(JOURNAL_FILE)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.error(f"journal error: {e}")


def _alert(text: str):
    """Копит тревоги для тренера — отправятся ближайшим джобом."""
    logger.warning(f"[INTEGRITY] {text}")
    _INTEGRITY_ALERTS.append(text)
    if len(_INTEGRITY_ALERTS) > 40:
        del _INTEGRITY_ALERTS[:-40]


def pending_add(kind: str, payload: dict):
    """Кладёт неудавшуюся операцию в очередь повтора."""
    try:
        d = os.path.dirname(PENDING_FILE)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(PENDING_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
                "kind": kind, **payload}, ensure_ascii=False) + "\n")
        logger.warning(f"pending_add {kind}: {payload}")
    except Exception as e:
        logger.error(f"pending_add error: {e}")


def pending_read_and_clear() -> list:
    try:
        if not os.path.exists(PENDING_FILE):
            return []
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        os.remove(PENDING_FILE)
        out = []
        for l in lines:
            try:
                out.append(json.loads(l))
            except Exception:
                pass
        return out
    except Exception as e:
        logger.error(f"pending_read error: {e}")
        return []


def _row_to_progress(r: list) -> dict:
    def _i(idx):
        try:
            return int(r[idx]) if len(r) > idx and str(r[idx]).strip() else 0
        except Exception:
            return 0

    def _s(idx):
        return r[idx] if len(r) > idx and r[idx] else ""

    return {
        "streak": _i(3), "max_streak": _i(4), "xp": _i(5), "level": _i(6),
        "coins": _i(7), "last_workout": _s(8), "last_food": _s(9),
        "food_streak": _i(10), "food_max_streak": _i(11),
        "last_wbonus": _s(12), "last_fbonus": _s(13),
    }


def _merge_with_hw(user_id, p: dict, source: str) -> dict:
    """Рубеж 2. Если таблица отдала значение НИЖЕ несгораемого максимума —
    это повреждение данных. Поднимаем до максимума и зовём тренера."""
    hw = hw_get(user_id)
    if not hw:
        return p
    healed = dict(p)
    losses = []
    for f in _MONOTONIC_FIELDS:
        h = int(hw.get(f, 0) or 0)
        if h > int(healed.get(f, 0) or 0):
            losses.append(f"{f}: {healed.get(f, 0)} → {h}")
            healed[f] = h
    if losses:
        healed["level"] = _level_value_for(healed["xp"])
        # Стрик и даты тоже подтягиваем, если строка выглядит обнулённой
        if healed.get("streak", 0) == 0 and hw.get("streak", 0):
            healed["streak"] = int(hw.get("streak") or 0)
        for f in ("last_workout", "last_food", "last_wbonus", "last_fbonus"):
            if not healed.get(f) and hw.get(f):
                healed[f] = hw[f]
        _journal("HEAL", user_id, {"source": source, "losses": losses,
                                   "restored": healed})
        _alert(f"Восстановлен прогресс `{user_id}` ({source}): " + "; ".join(losses))
    return healed


# ── ПРОГРЕСС ─────────────────────────────────────────────────────────────────
def get_progress(user_id, strict: bool = False):
    """Возвращает (dict, row_index).

    strict=True  — для операций ЗАПИСИ. При сбое чтения бросает SheetUnavailable,
                   чтобы вызывающий код НИКОГДА не записал нули поверх данных.
    strict=False — для отображения. При сбое отдаёт лучшее известное значение
                   из HW-марок и НЕ кэширует его.
    """
    key    = f"prog_{user_id}"
    cached = _c_get(key, _TTL_PROGRESS)
    if cached is not None:
        return cached

    try:
        rows = _ws_rows("progress", _TTL_SHEET)
    except Exception as e:
        # ⛔ Раньше здесь начинался путь к потере данных — молчаливые нули.
        logger.error(f"get_progress READ FAIL {user_id}: {e}")
        if strict:
            raise SheetUnavailable(f"progress read failed for {user_id}: {e}")
        hw = hw_get(user_id)
        fallback = {**PROGRESS_EMPTY, **{k: v for k, v in hw.items() if k in PROGRESS_EMPTY}}
        return (fallback, 0)   # ← намеренно НЕ кэшируем

    for i, r in enumerate(rows[1:], start=2):
        if r and r[0] == str(user_id):
            p = _merge_with_hw(user_id, _row_to_progress(r), "чтение")
            hw_update(user_id, p)
            result = (p, i)
            _c_set(key, result)
            return result

    # Строки нет. Это либо новый пользователь, либо строка исчезла.
    hw = hw_get(user_id)
    if hw and int(hw.get("xp", 0) or 0) > 0:
        restored = {**PROGRESS_EMPTY,
                    **{k: v for k, v in hw.items() if k in PROGRESS_EMPTY}}
        restored["level"] = _level_value_for(restored["xp"])
        _journal("ROW_LOST", user_id, {"restored": restored})
        _alert(f"Пропала строка прогресса `{user_id}` — восстановлена из резерва "
               f"({restored['xp']} XP). Будет записана при следующем действии.")
        result = (restored, 0)
        if strict:
            return result
        return result

    result = (dict(PROGRESS_EMPTY), 0)
    _c_set(key, result)
    return result


def _progress_live_rows():
    """Свежее чтение листа progress без кэша — для записи."""
    return _gs_call(ws("progress").get_all_values)


def save_progress(user_id, name, username, streak, max_streak, xp, level, coins,
                  last_workout, last_food="", food_streak=0, food_max_streak=0,
                  last_wbonus="", last_fbonus="", allow_reset: bool = False):
    """Единственная точка записи прогресса. Атомарна относительно других записей
    и физически не способна уменьшить XP, монеты и рекорды стрика.

    allow_reset=True разрешает обнулить ТЕКУЩИЙ стрик (еженедельный сброс).
    """
    uid = str(user_id)
    new = {"streak": int(streak or 0), "max_streak": int(max_streak or 0),
           "xp": int(xp or 0), "level": int(level or 0), "coins": int(coins or 0),
           "last_workout": last_workout or "", "last_food": last_food or "",
           "food_streak": int(food_streak or 0),
           "food_max_streak": int(food_max_streak or 0),
           "last_wbonus": last_wbonus or "", "last_fbonus": last_fbonus or ""}

    with _SHEET_WRITE_LOCK:
        sheet = ws("progress")
        rows  = _progress_live_rows()          # ← свежие данные, не кэш

        idxs = [i for i, r in enumerate(rows, start=1)
                if r and str(r[0]).strip() == uid]
        current = _row_to_progress(rows[idxs[0] - 1]) if idxs else None

        # Если дублей несколько — сливаем максимум, лишние удалим ниже
        if len(idxs) > 1:
            for extra in idxs[1:]:
                dup = _row_to_progress(rows[extra - 1])
                for f in _MONOTONIC_FIELDS:
                    current[f] = max(current.get(f, 0), dup.get(f, 0))
            _alert(f"Найдено {len(idxs)} строк прогресса для `{uid}` — объединяю.")

        # ── Рубеж 2: монотонность ────────────────────────────────────────────
        floor = hw_get(uid)
        for f in _MONOTONIC_FIELDS:
            base = max(int((current or {}).get(f, 0) or 0), int(floor.get(f, 0) or 0))
            if new[f] < base:
                _journal("BLOCKED_DECREASE", uid,
                         {"field": f, "attempt": new[f], "kept": base})
                _alert(f"Заблокировано уменьшение `{f}` у `{uid}`: "
                       f"{new[f]} → оставлено {base}")
                new[f] = base
        # Текущий стрик обнуляем только осознанно
        if not allow_reset and current:
            if new["streak"] == 0 and current.get("streak", 0) > 0:
                new["streak"] = current["streak"]
            if new["food_streak"] == 0 and current.get("food_streak", 0) > 0:
                new["food_streak"] = current["food_streak"]
        new["max_streak"]      = max(new["max_streak"], new["streak"])
        new["food_max_streak"] = max(new["food_max_streak"], new["food_streak"])
        new["level"]           = _level_value_for(new["xp"])
        # Даты не затираем пустотой
        if current:
            for f in ("last_workout", "last_food", "last_wbonus", "last_fbonus"):
                if not new[f] and current.get(f):
                    new[f] = current[f]

        row = [uid, username or "", name or "", new["streak"], new["max_streak"],
               new["xp"], new["level"], new["coins"], new["last_workout"],
               new["last_food"], new["food_streak"], new["food_max_streak"],
               new["last_wbonus"], new["last_fbonus"]]

        if idxs:
            _gs_call(sheet.update, f"A{idxs[0]}:N{idxs[0]}", [row])
            for extra in sorted(idxs[1:], reverse=True):
                try:
                    _gs_call(sheet.delete_rows, extra, tries=2)
                except Exception:
                    pass
        else:
            _gs_call(sheet.append_row, row)

        _c_del(f"prog_{user_id}")
        _ws_invalidate("progress")
        hw_update(uid, new)
        hw_save()
        _journal("SAVE", uid, {"xp": new["xp"], "coins": new["coins"],
                               "streak": new["streak"],
                               "food_streak": new["food_streak"]})
    return new


def get_level_name(xp):
    cur_name  = "🫡 Протрузианец"
    cur_thr   = 0
    for thr, name in sorted(LEVELS.items()):
        if xp >= thr:
            cur_name = name
            cur_thr  = thr
        else:
            return cur_name, cur_thr, thr
    return cur_name, cur_thr, None


def _level_value_for(xp):
    v = 0
    for thr in sorted(LEVELS.keys()):
        if xp >= thr:
            v = thr
    return v


def parse_kg(text):
    """Целое число — для XP, силовых, возраста и т.п."""
    import re
    if not text:
        return 0
    m = re.search(r"\d+(?:[.,]\d+)?", str(text).replace(",", "."))
    if m:
        try:
            return max(0, int(round(float(m.group()))))
        except Exception:
            return 0
    return 0


def plural_days(n: int) -> str:
    """Правильное склонение слова 'день' по числу: 1 день, 2 дня, 5 дней."""
    n = abs(int(n))
    if 11 <= (n % 100) <= 14:
        return "дней"
    last = n % 10
    if last == 1:
        return "день"
    if 2 <= last <= 4:
        return "дня"
    return "дней"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Универсальное склонение: 1 раз, 2 раза, 5 раз."""
    n = abs(int(n))
    if 11 <= (n % 100) <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def parse_weight_float(text):
    """Точный вес с десятичными — до тысячных. Принимает 82,35 или 82.350."""
    import re
    if not text:
        return 0.0
    clean = str(text).replace(",", ".").replace(" ", "")
    m = re.search(r"\d+(?:\.\d+)?", clean)
    if m:
        try:
            val = round(float(m.group()), 3)
            return val if val > 0 else 0.0
        except Exception:
            return 0.0
    return 0.0


# ── ЯДРО НАЧИСЛЕНИЯ XP (синхронно, вызывать только под _user_lock) ───────────
def _credit_xp_core(user_id, name, username, xp_amount, coins_amount=0):
    """Read-modify-write без гонок (вызывать только под _user_lock).
    strict=True: при сбое чтения бросаем исключение вместо записи нулей."""
    p, _ = get_progress(user_id, strict=True)
    old_xp    = p["xp"]
    new_xp    = old_xp + xp_amount
    new_coins = p["coins"] + coins_amount
    new_level = _level_value_for(new_xp)
    save_progress(user_id, name, username,
                  p["streak"], p["max_streak"], new_xp, new_level, new_coins,
                  p["last_workout"], last_food=p["last_food"],
                  food_streak=p["food_streak"], food_max_streak=p["food_max_streak"],
                  last_wbonus=p["last_wbonus"], last_fbonus=p["last_fbonus"])
    update_client_stats(user_id, "xp", xp_amount)
    old_ln, _, _ = get_level_name(old_xp)
    new_ln, _, _ = get_level_name(new_xp)
    return old_ln, new_ln


async def add_xp(user_id, name, username, xp_amount, coins_amount=0, context=None):
    """Потокобезопасное начисление XP/монет + уведомление о повышении уровня.
    При сбое Google Sheets начисление НЕ теряется — уходит в очередь повтора."""
    try:
        async with _user_lock(user_id):
            old_ln, new_ln = await asyncio.to_thread(
                _credit_xp_core, user_id, name, username, xp_amount, coins_amount
            )
    except Exception as e:
        logger.error(f"add_xp failed {user_id}: {e}")
        pending_add("xp", {"uid": str(user_id), "name": name or "",
                           "username": username or "",
                           "xp": int(xp_amount or 0), "coins": int(coins_amount or 0)})
        _alert(f"Начисление {xp_amount} XP для `{user_id}` отложено (сбой Sheets). "
               "Будет применено автоматически.")
        return False
    if context and old_ln != new_ln and new_ln in LEVEL_UP_MESSAGES:
        display = get_display_name(user_id, name)
        await safe_send(context, user_id,
                        f"🎉 *{md_safe(display)}, новый ранг!*\n\n"
                        f"{LEVEL_UP_MESSAGES[new_ln]}")


# ── ПРОГРЕСС ТРЕНИРОВКИ ───────────────────────────────────────────────────────
def _workout_progress_core(user_id, name, username, workout_date=None):
    """Синхронная часть — вызывать только под _user_lock + to_thread."""
    p, _ = get_progress(user_id, strict=True)
    wdate     = workout_date or datetime.now(TIMEZONE).date()
    wdate_str = wdate.strftime("%Y-%m-%d")

    last = None
    if p["last_workout"]:
        try:
            last = datetime.strptime(p["last_workout"], "%Y-%m-%d").date()
        except Exception:
            pass

    new_streak = p["streak"] if last == wdate else (p["streak"] or 0) + 1
    new_last   = wdate_str if (last is None or wdate > last) else p["last_workout"]

    old_xp    = p["xp"]
    new_xp    = old_xp + XP_PER_WORKOUT
    new_coins = p["coins"] + COINS_PER_WORKOUT

    save_progress(user_id, name, username,
                  new_streak, max(p["max_streak"], new_streak),
                  new_xp, _level_value_for(new_xp), new_coins, new_last,
                  last_food=p["last_food"], food_streak=p["food_streak"],
                  food_max_streak=p["food_max_streak"],
                  last_wbonus=p["last_wbonus"], last_fbonus=p["last_fbonus"])
    update_client_stats(user_id, "workouts", 1)
    update_client_stats(user_id, "xp", XP_PER_WORKOUT)

    old_ln, _, _ = get_level_name(old_xp)
    new_ln, _, _ = get_level_name(new_xp)
    return {"streak": new_streak, "xp": new_xp, "coins": new_coins,
            "old_level": old_ln, "new_level": new_ln}


async def update_workout_progress(user_id, name, username, context, workout_date=None):
    try:
        async with _user_lock(user_id):
            r = await asyncio.to_thread(
                _workout_progress_core, user_id, name, username, workout_date
            )
    except Exception as e:
        # Тренировка в лист workouts уже записана — начисление не теряем,
        # ставим в очередь повтора и отвечаем по последним известным данным.
        logger.error(f"update_workout_progress failed {user_id}: {e}")
        pending_add("workout_xp", {"uid": str(user_id), "name": name or "",
                                   "username": username or "",
                                   "xp": XP_PER_WORKOUT, "coins": COINS_PER_WORKOUT})
        _alert(f"Прогресс тренировки `{user_id}` отложен (сбой Sheets) — "
               "будет применён автоматически.")
        hw = hw_get(user_id)
        xp = int(hw.get("xp", 0) or 0) + XP_PER_WORKOUT
        ln = get_level_name(xp)[0]
        return (int(hw.get("streak", 0) or 0) + 1, xp,
                int(hw.get("coins", 0) or 0) + COINS_PER_WORKOUT, ln, ln)
    if r["old_level"] != r["new_level"] and r["new_level"] in LEVEL_UP_MESSAGES:
        display = get_display_name(user_id, name)
        await safe_send(context, user_id,
                        f"🎉 *{md_safe(display)}, новый ранг!*\n\n"
                        f"{LEVEL_UP_MESSAGES[r['new_level']]}")
    return r["streak"], r["xp"], r["coins"], r["old_level"], r["new_level"]


# ── ПРОГРЕСС ПИТАНИЯ ──────────────────────────────────────────────────────────
def _food_progress_core(user_id, name, username):
    p, _ = get_progress(user_id, strict=True)
    today = datetime.now(TIMEZONE).date()
    last  = None
    if p["last_food"]:
        try:
            last = datetime.strptime(p["last_food"], "%Y-%m-%d").date()
        except Exception:
            pass
    if last == today:
        new_fs = p["food_streak"]
    elif last == today - timedelta(days=1):
        new_fs = p["food_streak"] + 1
    else:
        new_fs = 1
    new_fmax = max(p["food_max_streak"], new_fs)
    save_progress(user_id, name, username, p["streak"], p["max_streak"],
                  p["xp"], p["level"], p["coins"], p["last_workout"],
                  last_food=today.strftime("%Y-%m-%d"),
                  food_streak=new_fs, food_max_streak=new_fmax,
                  last_wbonus=p["last_wbonus"], last_fbonus=p["last_fbonus"])
    return new_fs


async def update_food_progress(user_id, name, username, context):
    try:
        async with _user_lock(user_id):
            return await asyncio.to_thread(_food_progress_core, user_id, name, username)
    except Exception as e:
        logger.error(f"update_food_progress failed {user_id}: {e}")
        pending_add("food_xp", {"uid": str(user_id), "name": name or "",
                                "username": username or "",
                                "xp": 0, "coins": 0})
        _alert(f"Стрик питания `{user_id}` не сохранён (сбой Sheets) — повтор в очереди.")
        return int(hw_get(user_id).get("food_streak", 0) or 0)


def _week_start_str():
    today = datetime.now(TIMEZONE).date()
    return (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")


# ═════════════════════════════════════════════════════════════════════════════
# ГРАНИЦЫ НЕДЕЛИ — единый источник правды
# Отчёт приходит в ПОНЕДЕЛЬНИК утром, но подводит итоги ПРОШЛОЙ недели
# (пн 00:00 — вс 23:59). Иначе в понедельник считались бы данные за
# несколько часов новой недели — и отчёт был бы пустым.
# ═════════════════════════════════════════════════════════════════════════════
def current_week_bounds(today=None):
    """Понедельник — воскресенье недели, в которой находится дата."""
    today = today or datetime.now(TIMEZONE).date()
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=6)


def prev_week_bounds(today=None):
    """Полная ПРОШЛАЯ неделя: пн — вс."""
    today = today or datetime.now(TIMEZONE).date()
    start = today - timedelta(days=today.weekday()) - timedelta(days=7)
    return start, start + timedelta(days=6)


def report_week_bounds(today=None):
    """Отчётная неделя.
    Понедельник → подводим итоги прошлой недели (пн-вс).
    Остальные дни → текущая неделя (пн — сегодня)."""
    today = today or datetime.now(TIMEZONE).date()
    if today.weekday() == 0:
        return prev_week_bounds(today)
    start, _ = current_week_bounds(today)
    return start, today


def fmt_period(ws_, we_) -> str:
    return f"{ws_.strftime('%d.%m')} — {we_.strftime('%d.%m.%Y')}"


def _food_streak_bonus_core(user_id, name, username):
    p, _ = get_progress(user_id, strict=True)
    if p["food_streak"] > 0 and p["food_streak"] % 7 == 0:
        wk = _week_start_str()
        if p["last_fbonus"] != wk:
            nx = p["xp"] + XP_STREAK_WEEK
            nc = p["coins"] + COINS_STREAK_WEEK
            save_progress(user_id, name, username, p["streak"], p["max_streak"],
                          nx, _level_value_for(nx), nc, p["last_workout"],
                          last_food=p["last_food"], food_streak=p["food_streak"],
                          food_max_streak=p["food_max_streak"],
                          last_wbonus=p["last_wbonus"], last_fbonus=wk)
            update_client_stats(user_id, "xp", XP_STREAK_WEEK)
            return True
    return False


async def check_food_streak_bonus(user_id, name, username, context):
    try:
        async with _user_lock(user_id):
            granted = await asyncio.to_thread(_food_streak_bonus_core, user_id, name, username)
    except Exception as e:
        logger.error(f"check_food_streak_bonus failed {user_id}: {e}")
        return
    if granted:
        p, _ = get_progress(user_id)
        display = get_display_name(user_id, name)
        await safe_send(context, user_id,
                        f"🥗 *{md_safe(display)}, {p['food_streak']} дней питания подряд!*\n\n"
                        f"Это не случайность — это дисциплина.\n\n"
                        f"🎁 Награда: *+{XP_STREAK_WEEK} XP* и 🪙 *+{COINS_STREAK_WEEK}*\n\n"
                        "_Питание × дисциплина = результат. Продолжай!_ 💪")


def _workout_streak_bonus_core(user_id, name, username):
    p, _ = get_progress(user_id, strict=True)
    try:
        cl      = next((c for c in get_all_clients() if c and c[0] == str(user_id)), None)
        planned = planned_workouts_per_week(cl) if cl else 0
    except Exception:
        planned = 0
    if planned <= 0:
        return False, 0
    today      = datetime.now(TIMEZONE).date()
    week_start = today - timedelta(days=today.weekday())
    fact = 0
    try:
        for r in _ws_rows("workouts", _TTL_SHEET)[1:]:
            if len(r) > 1 and r[1] == str(user_id):
                try:
                    d = datetime.strptime(r[0].split()[0], "%d.%m.%Y").date()
                    if week_start <= d <= today:
                        fact += 1
                except Exception:
                    pass
    except Exception:
        pass
    if fact >= planned:
        wk = _week_start_str()
        if p["last_wbonus"] != wk:
            nx = p["xp"] + XP_STREAK_WEEK
            nc = p["coins"] + COINS_STREAK_WEEK
            save_progress(user_id, name, username, p["streak"], p["max_streak"],
                          nx, _level_value_for(nx), nc, p["last_workout"],
                          last_food=p["last_food"], food_streak=p["food_streak"],
                          food_max_streak=p["food_max_streak"],
                          last_wbonus=wk, last_fbonus=p["last_fbonus"])
            update_client_stats(user_id, "xp", XP_STREAK_WEEK)
            return True, planned
    return False, planned


async def check_workout_streak_bonus(user_id, name, username, context):
    try:
        async with _user_lock(user_id):
            granted, planned = await asyncio.to_thread(
                _workout_streak_bonus_core, user_id, name, username
            )
    except Exception as e:
        logger.error(f"check_workout_streak_bonus failed {user_id}: {e}")
        return
    if granted:
        display = get_display_name(user_id, name)
        await safe_send(context, user_id,
                        f"🏆 *{md_safe(display)}, план недели закрыт!*\n\n"
                        f"*{planned}* из *{planned}* {plural(planned, 'тренировки', 'тренировок', 'тренировок')} выполнено — без отмазок 🔥\n\n"
                        f"🎁 Бонус: *+{XP_STREAK_WEEK} XP* и 🪙 *+{COINS_STREAK_WEEK}*\n\n"
                        "_Братство видит тех, кто делает. И ты — один из них._ 💪")


def get_total_workouts(user_id):
    try:
        rows = _ws_rows("workouts", _TTL_SHEET)
        return sum(1 for r in rows[1:] if len(r) > 1 and r[1] == str(user_id))
    except Exception:
        return 0


# ── ПЕРСОНАЛИЗАЦИЯ ────────────────────────────────────────────────────────────
_ACTIVITY_MULT = [("сиж", 1.3), ("пешк", 1.45), ("труд", 1.6)]


def _anketa_row(user_id):
    try:
        rows = _ws_rows("anketa", _TTL_SHEET)
        return next((r for r in rows[1:] if r and r[0] == str(user_id)), None)
    except Exception:
        return None


def _anketa_field(row, key):
    try:
        return row[2 + ANKETA_KEYS.index(key)] if row else ""
    except Exception:
        return ""


def compute_nutrition_targets(user_id):
    """Mifflin-St Jeor → TDEE → КБЖУ-цель.
    Возвращает dict с kcal/protein/fat/carbs/mode или None."""
    row = _anketa_row(user_id)
    if not row:
        return None
    age    = parse_kg(_anketa_field(row, "age"))
    height = parse_kg(_anketa_field(row, "height"))
    weight = get_user_current_weight(user_id) or parse_kg(_anketa_field(row, "weight"))
    target = get_user_target_weight(user_id)   or parse_kg(_anketa_field(row, "target_weight"))
    if not (age and height and weight) or age > 100 or height < 120 or weight < 35:
        return None

    is_f = "женск" in _anketa_field(row, "gender").lower()
    bmr  = 10 * weight + 6.25 * height - 5 * age + (-161 if is_f else 5)

    mult = 1.45
    act  = _anketa_field(row, "activity").lower()
    for token, val in _ACTIVITY_MULT:
        if token in act:
            mult = val; break
    tdee = bmr * mult

    mode = "поддержание"
    kcal = tdee
    prot_mult = 1.8
    if target and (weight - target) >= 1:
        mode = "снижение веса"
        kcal = tdee * 0.83
        prot_mult = 2.0
    elif target and (target - weight) >= 1:
        mode = "набор массы"
        kcal = tdee * 1.10
        prot_mult = 1.8

    kcal    = max(int(round(kcal / 10) * 10), int(bmr))
    protein = int(round(weight * prot_mult))
    fat     = int(round(weight * 0.9))
    carbs   = max(0, int(round((kcal - fat * 9 - protein * 4) / 4)))
    return {"bmr": int(bmr), "tdee": int(tdee), "kcal": kcal,
            "protein": protein, "fat": fat, "carbs": carbs,
            "mode": mode, "weight": weight, "target": target}


def get_coaching_style(user_id):
    """Q7 теста целей → стиль работы с тренером."""
    try:
        rows = _ws_rows("goals", _TTL_SHEET)
        for r in rows[1:]:
            if r and r[0] == str(user_id) and len(r) > 9 and r[9]:
                return r[9]
    except Exception:
        pass
    return ""


def _style_tone(style):
    s = (style or "").lower()
    if any(w in s for w in ["жёстк", "жестк", "пинай", "контрол"]):
        return "Стиль: жёстко и прямо, требовательно, без сюсюканья."
    if any(w in s for w in ["мягк", "поддерж", "направл"]):
        return "Стиль: мягко, поддерживающе, тепло."
    if "план" in s:
        return "Стиль: по делу, дай чёткий план без лишних слов."
    return "Стиль: дружелюбно, с поддержкой, по-человечески."


def get_psych_type(user_id: int) -> str:
    """Психотип по вопросу 'psych' в анкете.
    Возвращает: 'achievement' | 'community' | 'progress' | 'health' | 'goal' | 'default'"""
    try:
        row = _anketa_row(user_id)
        val = (_anketa_field(row, "psych") or "").lower()
        if "рекорд" in val or "лучш" in val:
            return "achievement"
        if "стая" in val or "братств" in val or "группы" in val or "поддержк" in val:
            return "community"
        if "цифр" in val or "данн" in val or "прогресс" in val:
            return "progress"
        if "самочувств" in val or "силь" in val or "энерги" in val:
            return "health"
        if "цель" in val or "задач" in val:
            return "goal"
    except Exception:
        pass
    return "default"


# Пулы фраз по психотипам — используются в пуш-функциях
_PSYCH_PHRASES = {
    "achievement": {
        "morning_train":  ("🏆", "{name}, сегодня день для рекорда.", "_Покажи на что способен. Братство смотрит._"),
        "morning_rest":   ("🥩", "{name}, день восстановления.", "_Мышцы растут — завтра новый рекорд._"),
        "skip":           ("🏅", "{name}, давно не было рекордов.", "_3+ дня простоя — пора взять реванш._"),
        "food":           ("💪", "{name}, белок для рекордов?", "_Без топлива не будет роста._"),
    },
    "community": {
        "morning_train":  ("💪", "{name}, братство собирается!", "_Братство уже на разминке — присоединяйся._"),
        "morning_rest":   ("😌", "Доброе утро, {name}!", "_День отдыха. Покорми себя — братство держит темп._"),
        "skip":           ("🤝", "{name}, братство скучает.", "_Возвращайся — без осуждения, просто вперёд._"),
        "food":           ("🥗", "{name}, питание — часть командной игры.", "_Сдай отчёт — братство тебя видит._"),
    },
    "progress": {
        "morning_train":  ("📊", "{name}, сегодня +{xp} XP к прогрессу.", "_Каждая тренировка — это цифры._"),
        "morning_rest":   ("📈", "{name}, день восстановления.", "_Прогресс = тренировки + отдых + питание._"),
        "skip":           ("📉", "{name}, {dw} дней без данных.", "_Пробел в статистике — заполни его сегодня._"),
        "food":           ("📊", "{name}, КБЖУ за сегодня?", "_Без данных нет прогресса. 2 минуты._"),
    },
    "health": {
        "morning_train":  ("💚", "Доброе утро, {name}!", "_Сегодня тренировка — подарок своему телу._"),
        "morning_rest":   ("🌿", "{name}, день заряда.", "_Тело работает даже в отдых. Питание — помоги ему._"),
        "skip":           ("💛", "{name}, как ты?", "_Несколько дней без тренировки. Тело скучает._"),
        "food":           ("🥩", "{name}, покорми тело.", "_Хорошее питание = хорошее самочувствие._"),
    },
    "goal": {
        "morning_train":  ("🎯", "{name}, шаг к цели.", "_Сегодняшняя тренировка — прямой маршрут._"),
        "morning_rest":   ("🔋", "{name}, перезаряжаемся.", "_Отдых — часть пути к цели._"),
        "skip":           ("⏳", "{name}, цель никуда не ушла.", "_Но каждый день паузы — это расстояние до неё._"),
        "food":           ("🎯", "{name}, питание = топливо для цели.", "_Сдай отчёт и держи курс._"),
    },
    "default": {
        "morning_train":  ("🔥", "{name}, сегодня твой день.", "_Просто начни — остальное само пойдёт._"),
        "morning_rest":   ("😌", "Доброе утро, {name}!", "_День восстановления. Мышцы растут сейчас._"),
        "skip":           ("💛", "{name}, давно не виделись.", "_Не страшно. Один шаг — и стрик живёт._"),
        "food":           ("🥗", "{name}, питание за сегодня?", "_Одно фото — и день не ноль._"),
    },
}

def get_psych_phrases(user_id: int) -> dict:
    """Возвращает пул фраз по психотипу пользователя."""
    psych = get_psych_type(user_id)
    return _PSYCH_PHRASES.get(psych, _PSYCH_PHRASES["default"])


def compute_churn_risk(user_id: int) -> dict:
    """Прогноз риска ухода клиента. Без запросов к Sheets — только кэш.
    Возвращает: {risk: 0-100, level: ok/warning/critical, reasons: [...]}"""
    try:
        p, _  = get_progress(user_id)
        today = datetime.now(TIMEZONE).date()

        def _ds(s):
            if not s:
                return 999
            try:
                return (today - datetime.strptime(s, "%Y-%m-%d").date()).days
            except Exception:
                return 999

        dw      = _ds(p.get("last_workout", ""))
        df      = _ds(p.get("last_food",    ""))
        streak  = p.get("streak", 0)
        fstreak = p.get("food_streak", 0)
        xp      = p.get("xp", 0)

        risk    = 0
        reasons = []

        # Нет тренировки давно
        if dw >= 10:
            risk += 45; reasons.append(f"нет тренировки {dw} {plural_days(dw)}")
        elif dw >= 5:
            risk += 25; reasons.append(f"нет тренировки {dw} {plural_days(dw)}")
        elif dw >= 3:
            risk += 10

        # Стрик тренировок нулевой
        if streak == 0 and dw > 2:
            risk += 20; reasons.append("стрик обнулён")

        # Питание пропускает
        if df >= 7:
            risk += 15; reasons.append(f"нет питания {df} {plural_days(df)}")

        # Профиль не заполнен (нет XP от анкеты)
        if xp < 150:
            risk += 10; reasons.append("профиль не заполнен")

        # Новичок уже пропускает
        cl = _get_client_row(user_id)
        if cl and len(cl) > 3 and cl[3]:
            try:
                reg_date = datetime.strptime(cl[3], "%d.%m.%Y").date()
                days_in = (today - reg_date).days
                if days_in < 14 and dw >= 3:
                    risk += 15; reasons.append("новичок, уже пропускает")
            except Exception:
                pass

        risk  = min(risk, 100)
        level = "critical" if risk >= 70 else "warning" if risk >= 35 else "ok"
        return {"risk": risk, "level": level, "reasons": reasons}
    except Exception:
        return {"risk": 0, "level": "ok", "reasons": []}


def build_coach_context(user_id):
    """Полный контекст клиента для Claude. Включает ВСЕ ответы анкеты,
    полные цели, КБЖУ, психотип, стрики, рекорды, восстановление."""
    parts = []
    row   = _anketa_row(user_id)
    if row:
        # Все значимые поля анкеты
        ANKETA_LABELS = {
            "name": "Имя", "age": "Возраст", "gender": "Пол",
            "height": "Рост", "weight": "Вес при регистрации",
            "target_weight": "Желаемый вес",
            "health": "Здоровье/ограничения",
            "nutrition": "Стиль питания", "sleep": "Сон",
            "stress": "Уровень стресса", "alcohol": "Алкоголь",
            "activity": "Активность вне зала",
            "experience": "Опыт тренировок",
            "motivation": "Мотивация",
            "extra": "Доп. информация",
        }
        for key, label in ANKETA_LABELS.items():
            val = _anketa_field(row, key)
            if val and val.strip().lower() not in ("нет", "-", "—", ""):
                if key == "health":
                    parts.append(f"⚠️ {label}: {val}")
                else:
                    parts.append(f"{label}: {val}")

    # Полные цели (не summary)
    try:
        g_rows = _ws_rows("goals", _TTL_SHEET)
        for r in g_rows[1:]:
            if r and r[0] == str(user_id):
                labels = ["Главное качество", "Второе качество", "Формат тренировок",
                          "Результат за 3 месяца", "Что мешает", "Дедлайн",
                          "Глубинная цель", "Стиль работы с тренером"]
                vals = r[2:10]
                for i in range(min(len(vals), len(labels))):
                    if vals[i]:
                        parts.append(f"Цель — {labels[i]}: {vals[i][:100]}")
                break
    except Exception:
        pass

    # Текущий вес и КБЖУ
    t = compute_nutrition_targets(user_id)
    if t:
        parts.append(f"Текущий вес: {t['weight']} кг → цель: {t.get('target', '?')} кг")
        parts.append(f"Режим: {t['mode']}")
        parts.append(f"Норма: ~{t['kcal']} ккал, Б {t['protein']}г, Ж {t['fat']}г, У {t['carbs']}г")

    # Психотип
    psych = get_psych_type(user_id)
    if psych != "default":
        psych_labels = {
            "achievement": "мотивация рекордами и достижениями",
            "community":   "важна поддержка группы и принадлежность",
            "progress":    "ориентирован на цифры и прогресс",
            "health":      "фокус на самочувствии и здоровье",
            "goal":        "чёткая цель, идёт к ней планомерно",
        }
        parts.append(f"Психотип: {psych_labels.get(psych, psych)}")

    # Стрики и активность
    try:
        p, _ = get_progress(user_id)
        if p["streak"] > 0:
            parts.append(f"Стрик тренировок: {p['streak']} {plural_days(p['streak'])}")
        if p["food_streak"] > 0:
            parts.append(f"Стрик питания: {p['food_streak']} {plural_days(p['food_streak'])}")
        total = get_total_workouts(user_id)
        if total:
            parts.append(f"Всего тренировок: {total}")
    except Exception:
        pass

    # Рекорды
    try:
        rec = get_last_records(user_id)
        if rec and rec.get("sum") not in (None, "", "0"):
            parts.append(f"Рекорды: присед {rec['squat']}кг, жим {rec['bench']}кг, "
                         f"тяга {rec['deadlift']}кг (сумма {rec['sum']}кг)")
    except Exception:
        pass

    if not parts:
        return ""
    return "ПРОФИЛЬ КЛИЕНТА (обязательно учитывай всё):\n" + "\n".join(f"- {p}" for p in parts)


# ── CLAUDE — ТРЕНИРОВКИ ───────────────────────────────────────────────────────
# Все функции Claude синхронны — вызывать через asyncio.to_thread.

def analyze_workout(text, history=None, coach_context="", style="", strength_chart=""):
    """Анализ тренировки. В таблицу пишется 2 колонки:
      col4 = assessment   — AI-отчёт (до 300 символов)
      col5 = strength_log — только силовые/гипертрофия (до 1000 символов)
    """
    try:
        history_block = ""
        if history:
            lines = ["ИСТОРИЯ ПОСЛЕДНИХ ТРЕНИРОВОК:"]
            for h in history[-5:]:
                best = h.get("strength_log") or h.get("log") or ""
                if best:
                    lines.append(f"  {h['date']}: {best}")
            history_block = "\n".join(lines) + "\n\n"

        ctx_block   = f"{coach_context}\n\n" if coach_context else ""
        chart_block = f"{strength_chart}\n\n" if strength_chart else ""

        system = (
            "Ты личный тренер. " + _style_tone(style) + "\n"
            "Учитывай профиль клиента: цель, опыт, здоровье, прогрессию.\n\n"
            "Ответь СТРОГО JSON без markdown и комментариев:\n"
            "{\n"
            '  "completed": "да/нет/частично",\n'
            '  "assessment": "2-3 предложения. Сравни с историей, '
            'отметь прогресс или спад, свяжи с целью. Тепло и конкретно.",\n'
            '  "notes": "1 замечание по технике или восстановлению.",\n'
            '  "next_focus": "1 подсказка на следующую тренировку: '
            '+2.5кг в приседе, добавить RDL, сократить паузу и т.п.",\n'
            '  "strength_log": "ТОЛЬКО основная часть и гипертрофия. '
            'Базовые многосуставные + изоляция с весами. '
            'НЕ включай: разминку, кардио, растяжку, мобилити, кор, планки, пресс. '
            'Формат: Присед 3х5х100кг | Жим лёжа 4х8х70кг | Тяга 3х5х120кг. '
            'Пустая строка если таких не было."\n'
            "}"
        )

        content = f"{ctx_block}{chart_block}{history_block}ТРЕНИРОВКА:\n{text}"
        m = claude.messages.create(
            model=CLAUDE_MODEL, max_tokens=600, system=system,
            messages=[{"role": "user", "content": content}],
        )
        raw = m.content[0].text
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
        result = json.loads(raw.strip())
        result.setdefault("strength_log", "")
        result.setdefault("next_focus", "")
        return result
    except Exception as e:
        logger.error(f"analyze_workout error: {e}")
        return {"completed": "да", "assessment": "Тренировка записана.",
                "notes": "", "next_focus": "", "strength_log": ""}


def analyze_food_report(meals_text, photos_b64=None, targets=None, coach_context=""):
    """Анализ питания с учётом КБЖУ-нормы и профиля клиента.
    Только Claude — вызывать через asyncio.to_thread."""
    try:
        content_parts = []
        if photos_b64:
            for img_b64 in photos_b64:
                content_parts.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64},
                })

        target_block = ""
        if targets:
            target_block = (
                f"\n\nЦЕЛЕВЫЕ НОРМЫ КЛИЕНТА (режим: {targets['mode']}):\n"
                f"- Калории: {targets['kcal']} ккал\n"
                f"- Белок: {targets['protein']} г\n"
                f"- Жиры: {targets['fat']} г\n"
                f"- Углеводы: {targets['carbs']} г\n"
                "В блоке СРАВНЕНИЕ С НОРМОЙ: сопоставь факт с этими числами "
                "и дай 1 конкретный совет, как добрать (особенно белок)."
            )
        ctx_block = f"\n\n{coach_context}" if coach_context else ""
        final_text = meals_text if meals_text else "см. фото выше"
        content_parts.append({"type": "text",
                               "text": f"Питание за день:\n{final_text}{target_block}{ctx_block}"})

        m = claude.messages.create(
            model=CLAUDE_MODEL, max_tokens=800,
            system="""Ты нутрициолог. Коротко, тезисно, по делу.

⚠️ ПРАВИЛА ПОДСЧЁТА:
- ВСЕГДА добавляй +20% к итоговым калориям (масло, соусы, заправки, допорции)
- Порции бери по ВЕРХНЕЙ границе
- Мужчина редко ест менее 2200 ккал/день — если вышло меньше, пересчитай
- Все фото и текст = ОДИН день. Суммируй всё.

⚠️ ФОРМАТ:
- НЕ используй markdown-таблицы (| col | col |) — Telegram их не рендерит
- Только *жирный*, _курсив_, эмодзи и текст построчно
- Максимум 1.5 экрана телефона — будь краток

СТРОГИЙ ФОРМАТ ОТВЕТА:

🍽 *РАЦИОН ЗА ДЕНЬ:*
• Блюдо — ккал
• Блюдо — ккал
(каждое на отдельной строке, коротко)
━━━━━━━━━━━━━
📊 *ИТОГО:*
🔥 *XXXX ккал* · 🥩 Б *XXг* · 🥑 Ж *XXг* · 🍚 У *XXг*
━━━━━━━━━━━━━
🎯 *НОРМА vs ФАКТ:*
🔥 Калории: факт → цель → _разница_
🥩 Белок: факт → цель → _разница_
(если норма дана. Если нет — пропусти.)
━━━━━━━━━━━━━
💡 *НА ЗАВТРА:*
• 1 конкретный совет что добавить/убрать/заменить
• Привязан к цели клиента

📈 _Короткая фраза поддержки — 1 строка_""",
            messages=[{"role": "user", "content": content_parts}],
        )
        return m.content[0].text
    except Exception as e:
        logger.error(f"analyze_food_report error: {e}")
        parts = " + ".join(filter(None, [
            f"📸 {len(photos_b64)} фото" if photos_b64 else "",
            "📝 текст" if meals_text else "",
        ]))
        return (f"✅ *Отчёт принят!* ({parts})\n\n"
                "⚠️ Анализ КБЖУ временно недоступен — попробуй позже.\n"
                "Главное — ты отчитался. Это уже дисциплина! 💪")


def get_user_goals_summary(user_id):
    try:
        rows = _ws_rows("goals", _TTL_SHEET)
        for r in rows[1:]:
            if r and r[0] == str(user_id):
                vals   = r[2:10]
                labels = ["Главное качество", "Второе качество", "Формат",
                          "Результат за 3 мес", "Что мешает", "Дедлайн",
                          "Глубинная цель", "Стиль работы"]
                parts  = [f"{labels[i]}: {vals[i]}"
                          for i in range(min(len(vals), len(labels))) if vals[i]]
                return "; ".join(parts) if parts else "не заполнены"
    except Exception:
        pass
    return "не заполнены"


def get_user_weight_history(user_id, weeks=8):
    try:
        rows = _ws_rows("weight_log", _TTL_SHEET)
        hist = []
        for r in rows[1:]:
            if len(r) > 2 and r[0] == str(user_id):
                kg = parse_kg(r[2])
                if kg > 0:
                    hist.append((r[1], kg))
        return hist[-weeks:]
    except Exception:
        return []


def get_user_target_weight(user_id):
    try:
        rows = _ws_rows("anketa", _TTL_SHEET)
        for r in rows[1:]:
            if r and r[0] == str(user_id) and len(r) > 7 and r[7]:
                return parse_weight_float(r[7]) or parse_kg(r[7])
    except Exception:
        pass
    return 0


async def _update_anketa_target_weight(user_id: int, new_target: float):
    """Обновляет цель по весу прямо в листе anketa."""
    try:
        col_idx = 2 + ANKETA_KEYS.index("target_weight") + 1  # +1 т.к. gspread 1-based
        sheet   = ws("anketa")
        ids     = sheet.col_values(1)
        if str(user_id) not in ids:
            return False
        row_idx = ids.index(str(user_id)) + 1
        await asyncio.to_thread(sheet.update_cell, row_idx, col_idx, str(new_target))
        _ws_invalidate("anketa")
        _c_del(f"prog_{user_id}")
        return True
    except Exception as e:
        logger.error(f"_update_anketa_target_weight error: {e}")
        return False


def get_user_current_weight(user_id):
    hist = get_user_weight_history(user_id, weeks=20)
    if hist:
        return hist[-1][1]
    try:
        rows = _ws_rows("anketa", _TTL_SHEET)
        for r in rows[1:]:
            if r and r[0] == str(user_id) and len(r) > 6 and r[6]:
                return parse_kg(r[6])
    except Exception:
        pass
    return 0


def count_food_days(user_id):
    try:
        rows = _ws_rows("food_log", _TTL_SHEET)
        days = set()
        for r in rows[1:]:
            if len(r) > 2 and r[0] == str(user_id) and r[2]:
                days.add(r[2])
        return len(days)
    except Exception:
        return 0


def get_workout_history(user_id, limit=10):
    try:
        rows      = _ws_rows("workouts", _TTL_SHEET)
        user_rows = [r for r in (rows[1:] if rows else [])
                     if len(r) > 1 and r[1] == str(user_id)]
        result = []
        for r in user_rows[-limit:]:
            assess      = r[3] if len(r) > 3 else ""
            strength_lg = r[4] if len(r) > 4 else ""
            if assess or strength_lg:
                result.append({
                    "date":         r[0],
                    "log":          assess,
                    "text":         "",
                    "strength_log": strength_lg,
                })
        return result
    except Exception as e:
        logger.error(f"get_workout_history error: {e}")
        return []


def get_strength_dynamics(user_id):
    import re
    history   = get_workout_history(user_id, limit=20)
    movements = {}
    PATTERNS  = [
        (r"[Пп]рисед[а-я]*\s*(?:\d+х\d+х)?(\d+)\s*(?:кг|kg)", "Присед"),
        (r"[Жж]им[а-я]*\s*(?:лёжа|лежа)?\s*(?:\d+х\d+х)?(\d+)\s*(?:кг|kg)", "Жим"),
        (r"[Тт]яг[а-я]*\s*(?:\d+х\d+х)?(\d+)\s*(?:кг|kg)", "Тяга"),
        (r"[Пп]одтяг[а-я]*.*?(?:\d+х\d+х)?(\d+)\s*(?:кг|kg)", "Подтяг"),
        (r"[Жж]им.*?стоя.*?(?:\d+х\d+х)?(\d+)\s*(?:кг|kg)", "Жим стоя"),
        (r"[Жж]им.*?гант[а-я]*\s*(?:\d+х\d+х)?(\d+)\s*(?:кг|kg)", "Жим гантелей"),
        (r"[Рр]умынск[а-я]*.*?(?:\d+х\d+х)?(\d+)\s*(?:кг|kg)", "RDL"),
    ]
    for entry in history:
        # Приоритет: strength_log (уже отфильтрован) > workout_log > user text
        src = entry.get("strength_log") or entry.get("log", "") + " " + entry.get("text", "")
        for pattern, name in PATTERNS:
            for m in re.finditer(pattern, src, re.IGNORECASE):
                try:
                    kg = int(m.group(1))
                    # Фильтр мусора: игнорируем слишком маленькие/большие значения
                    if 5 <= kg <= 500:
                        movements.setdefault(name, []).append((entry["date"], kg))
                except Exception:
                    pass
    return movements


def format_strength_chart(dynamics: dict) -> str:
    if not dynamics:
        return ""
    lines = ["📈 *Динамика силовых движений:*\n"]
    for movement, entries in dynamics.items():
        if len(entries) < 2:
            continue
        first_kg = entries[0][1]
        last_kg  = entries[-1][1]
        max_kg   = max(e[1] for e in entries)
        diff     = last_kg - first_kg
        sign     = "+" if diff >= 0 else ""
        bar_len  = min(10, int(last_kg / max_kg * 10)) if max_kg > 0 else 0
        bar      = "█" * bar_len + "░" * (10 - bar_len)
        lines.append(f"*{movement}:* {bar} {last_kg}кг ({sign}{diff}кг за {len(entries)} трен.)")
    return "\n".join(lines) if len(lines) > 1 else ""


# ── ЕЖЕНЕДЕЛЬНЫЙ ОТЧЁТ ───────────────────────────────────────────────────────
def _collect_weekly_data(user_id, week_start=None, week_end=None):
    """Собирает данные СТРОГО за указанный период [week_start; week_end].
    По умолчанию — отчётная неделя (в понедельник это ПРОШЛАЯ неделя целиком)."""
    if week_start is None or week_end is None:
        week_start, week_end = report_week_bounds()

    def _in_period(d):
        return d is not None and week_start <= d <= week_end

    f_rows = _ws_rows_safe("food_log", _TTL_ADMIN_READ)
    f_rows = f_rows[1:] if f_rows else []
    user_food = [r for r in f_rows if len(r) > 1 and r[0] == str(user_id)]
    # Только записи внутри отчётного периода
    period_food = [r for r in user_food
                   if len(r) > 2 and _in_period(_safe_date(r[2], "%d.%m.%Y"))]
    food_days_week = len({r[2] for r in period_food if len(r) > 2 and r[2]})

    w_rows = _ws_rows_safe("workouts", _TTL_ADMIN_READ)
    w_rows = w_rows[1:] if w_rows else []
    period_workouts = [
        r for r in w_rows
        if len(r) > 1 and r[1] == str(user_id)
        and _in_period(_safe_date(r[0].split()[0] if r[0] else "", "%d.%m.%Y"))
    ]
    workouts_week = len(period_workouts)

    # Силовые логи за период
    week_strength = [f"{r[0].split()[0]}: {r[4][:150]}"
                     for r in period_workouts if len(r) > 4 and r[4]]

    planned_n = 0
    try:
        cl = next((c for c in get_all_clients() if c and c[0] == str(user_id)), None)
        if cl:
            planned_n = planned_workouts_per_week(cl)
    except Exception:
        pass

    # Стрик и XP
    p, _ = get_progress(user_id)

    # Самочувствие — тоже строго за период
    wellbeing_note = ""
    trend = _get_wellbeing_range(user_id, week_start, week_end)
    if trend:
        bad = sum(1 for r in trend if _wellbeing_score(r) < 3)
        if bad >= 3:
            wellbeing_note = f"⚠️ {bad} из {len(trend)} дней — плохое восстановление"
        elif bad > 0:
            wellbeing_note = f"Восстановление: {len(trend)-bad}/{len(trend)} дней в норме"
        else:
            wellbeing_note = "Восстановление стабильное"

    return {
        "period_start":     week_start,
        "period_end":       week_end,
        "period_label":     fmt_period(week_start, week_end),
        "is_closed_week":   (week_end - week_start).days >= 6,
        "food_days_week":   food_days_week,
        "food_xp_week":     food_days_week * XP_PER_FOOD_DAY,
        "workouts_week":    workouts_week,
        "workout_xp_week":  workouts_week * XP_PER_WORKOUT,
        "planned_n":        planned_n,
        "goals_summary":    get_user_goals_summary(user_id),
        "weight_hist":      get_user_weight_history(user_id),
        "target_weight":    get_user_target_weight(user_id),
        "targets":          compute_nutrition_targets(user_id),
        "coach_context":    build_coach_context(user_id),
        "streak":           p.get("streak", 0),
        "food_streak":      p.get("food_streak", 0),
        "xp":               p.get("xp", 0),
        "wellbeing_note":   wellbeing_note,
        "week_strength":    "\n".join(week_strength[-5:]) if week_strength else "",
        "meals_summary":    "\n".join(
            f"- {r[2]}: " + (r[4][:120] if len(r) > 4 and r[4]
                             else r[3][:120] if len(r) > 3 else "запись")
            for r in period_food[-7:]
        ) or "нет записей по питанию за этот период",
    }


def _safe_date(s, fmt):
    try:
        return datetime.strptime(s, fmt).date()
    except Exception:
        return None


def _render_weekly_report(user_name, data):
    """Только Claude — без gspread. Безопасно запускать в to_thread."""
    try:
        ww     = data["workouts_week"]
        pn     = data["planned_n"]
        wh     = data["weight_hist"]
        tgt    = data["target_weight"]
        fxw    = data["food_xp_week"]
        wxw    = data["workout_xp_week"]
        t      = data.get("targets")
        ctx    = data.get("coach_context", "")

        plan_line   = f"{ww}/{pn}" if pn else f"{ww} (план не задан)"
        weight_line = ", ".join(f"{d}: {kg}кг" for d, kg in wh) if wh else "нет данных"
        tgt_line    = f"{tgt} кг" if tgt else "не указан"
        kbju_line   = ""
        if t:
            kbju_line = (f"НОРМА КБЖУ: {t['kcal']} ккал, белок {t['protein']}г, "
                         f"жиры {t['fat']}г, углеводы {t['carbs']}г (режим: {t['mode']})\n")

        strength_block = ""
        if data.get("week_strength"):
            strength_block = f"СИЛОВЫЕ ЛОГИ ЗА НЕДЕЛЮ:\n{data['week_strength']}\n\n"

        wellbeing_line = ""
        if data.get("wellbeing_note"):
            wellbeing_line = f"ВОССТАНОВЛЕНИЕ: {data['wellbeing_note']}\n"

        streak_line = ""
        if data.get("streak", 0) > 0:
            streak_line = f"СТРИК ТРЕНИРОВОК: {data['streak']} {plural_days(data['streak'])}\n"
        if data.get("food_streak", 0) > 0:
            streak_line += f"СТРИК ПИТАНИЯ: {data['food_streak']} {plural_days(data['food_streak'])}\n"

        period    = data.get("period_label", "")
        is_closed = data.get("is_closed_week", True)
        period_hdr = (
            f"ОТЧЁТНЫЙ ПЕРИОД: завершённая неделя {period} (пн-вс).\n"
            "Это итоги ПРОШЕДШЕЙ недели — говори о ней в прошедшем времени. "
            "Не упоминай текущий день и не подводи итоги начавшейся недели.\n"
            if is_closed else
            f"ОТЧЁТНЫЙ ПЕРИОД: текущая неделя {period} (неполная).\n"
        )

        prompt = (
            f"{ctx}\n\n" if ctx else ""
        ) + (
            f"Персональный еженедельный отчёт для {user_name}.\n"
            "Пиши тепло, по-человечески. Конкретно и полезно.\n\n"
            f"{period_hdr}"
            f"ЦЕЛИ: {data['goals_summary']}\n"
            f"ЖЕЛАЕМЫЙ ВЕС: {tgt_line}\n"
            f"{kbju_line}"
            f"ДИНАМИКА ВЕСА: {weight_line}\n"
            f"ТРЕНИРОВКИ (факт/план): {plan_line}\n"
            f"ДНЕЙ ПИТАНИЯ: {data['food_days_week']}\n"
            f"{streak_line}"
            f"{wellbeing_line}"
            f"\n{strength_block}"
            f"ПИТАНИЕ:\n{data['meals_summary']}\n\n"
            "Формат:\n"
            f"📊 *ИТОГИ НЕДЕЛИ {period}*\n\n"
            "🏋️ *Тренировки:* (1-2 предложения, сравни с планом, отметь силовые)\n"
            "🍽️ *Питание:* (2-3 предложения, если есть норма — сравни факт с ней)\n"
            "⚖️ *Динамика веса:* (1-2 предложения, связь с целью)\n"
            "😴 *Восстановление:* (1 предложение, если есть данные)\n"
            "✅ *Что было хорошо:* (2-3 пункта)\n"
            "⚠️ *Что улучшить:* (2-3 пункта, конкретно)\n"
            "🎯 *План на неделю:* (3 действия под цель клиента)\n\n"
            f"🪙 *Вклад за прошедшую неделю:*\n"
            f"• Питание: +{fxw} XP\n"
            f"• Тренировки: +{wxw} XP\n"
            f"• Всего: +{fxw + wxw} XP\n\n"
            "💪 Заверши мотивирующей фразой под психотип клиента."
        )
        m = claude.messages.create(
            model=CLAUDE_MODEL, max_tokens=900,
            system="Ты тренер-диетолог. Персональный отчёт с учётом профиля клиента. НЕ используй markdown-таблицы (| col |) — Telegram не поддерживает. Только *жирный*, _курсив_, списки.",
            messages=[{"role": "user", "content": prompt}],
        )
        return m.content[0].text
    except Exception:
        return None


# ── CLAUDE CLIENT ─────────────────────────────────────────────────────────────
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def generate_weekly_nutrition_report(user_id, user_name, week_start=None, week_end=None):
    """Публичная обёртка для внешнего вызова недельного отчёта.
    Без аргументов берёт отчётную неделю (в понедельник — прошлую целиком)."""
    return _render_weekly_report(
        user_name, _collect_weekly_data(user_id, week_start, week_end))


# ── HELPERS ───────────────────────────────────────────────────────────────────
def get_display_name(user_id, telegram_first_name):
    try:
        rows = _ws_rows("anketa", _TTL_SHEET)
        for r in rows[1:]:
            if r and r[0] == str(user_id) and len(r) > 2 and r[2]:
                return r[2]
    except Exception:
        pass
    return telegram_first_name or "друг"


def extract_food_summary(analysis_text: str) -> str:
    """Извлекает ПОЛНЫЙ рацион + КБЖУ из ответа Claude.
    Работает с любым форматом — парсит все блюда и итоговые цифры.
    Если парсинг не удался — возвращает весь ответ Claude целиком."""
    if not analysis_text:
        return ""
    import re
    lines = analysis_text.split("\n")
    meals = []
    kbju_line = ""

    # ── Собираем ВСЕ блюда (строки с • или — и ккал) ──
    for line in lines:
        s = line.strip()
        if not s:
            continue
        clean = s.replace("*", "").replace("_", "").strip()
        if (clean.startswith("•") or clean.startswith("-") or clean.startswith("–")) and "ккал" in clean.lower():
            meal = clean.lstrip("•-– ").strip()
            if len(meal) > 3:
                meals.append(meal)
        if "ккал" in clean and ("Б " in clean or "🥩" in clean or "белок" in clean.lower()):
            kbju_line = clean.replace("🔥", "").replace("🥩", "").replace("🥑", "").replace("🍚", "").strip()

    # ── Fallback: секции РАЦИОН ──
    if not meals:
        in_food_section = False
        for line in lines:
            s = line.strip().replace("*", "").replace("_", "")
            if not s:
                continue
            up = s.upper()
            if "РАЦИОН" in up or "СЪЕДЕН" in up or "ПРИЁМ" in up or "🍽" in s:
                in_food_section = True
                continue
            if "ИТОГО" in up or "НОРМА" in up or "ЗАВТРА" in up or "СОВЕТ" in up or "━" in s:
                in_food_section = False
                continue
            if in_food_section and len(s) > 5:
                meals.append(s.lstrip("•-– ").strip())

    # ── Fallback для КБЖУ ──
    if not kbju_line:
        for line in lines:
            s = line.strip().replace("*", "")
            m_kcal = re.search(r"(\d{3,4})\s*ккал", s)
            m_prot = re.search(r"[БбPp]\w*\s*(\d{2,3})", s)
            if m_kcal and m_prot:
                kbju_line = s.replace("🔥", "").replace("🥩", "").replace("🥑", "").replace("🍚", "").strip()
                break

    parts = []
    if meals:
        parts.append("; ".join(meals[:12]))
    if kbju_line:
        parts.append(kbju_line)

    if parts:
        return " | ".join(parts)[:1500]

    fallback = analysis_text.replace("*", "").replace("_", "").replace("━", "")
    fallback = re.sub(r'[🍽📊🔥🥩🥑🍚🎯💡📈✅⚠️]', '', fallback)
    return " ".join(fallback.split())[:1500]


# ── WARMUP ────────────────────────────────────────────────────────────────────
def get_warmup_transfer(user_id):
    try:
        rows = ws("warmup_transfer").get_all_values()
        if not rows:
            return None
        data_rows = rows if (rows[0] and rows[0][0] and rows[0][0].isdigit()) else rows[1:]
        for r in data_rows:
            if r and r[0] == str(user_id):
                answers = {}
                if len(r) > 3 and r[3]:
                    try:
                        answers = json.loads(r[3])
                    except Exception:
                        pass
                return {
                    "xp":      int(r[1]) if len(r) > 1 and r[1] else 0,
                    "coins":   int(r[2]) if len(r) > 2 and r[2] else 0,
                    "answers": answers,
                }
    except Exception as e:
        logger.error(f"get_warmup_transfer error: {e}")
    return None


def warmup_already_imported(user_id) -> bool:
    try:
        return str(user_id) in ws("warmup_imported").col_values(1)
    except Exception:
        return False


def mark_warmup_imported(user_id, xp, coins):
    try:
        ids = ws("warmup_imported").col_values(1)
        if str(user_id) in ids:
            return
        ws("warmup_imported").append_row([
            str(user_id), str(xp), str(coins),
            datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M"),
        ])
    except Exception as e:
        logger.error(f"mark_warmup_imported error: {e}")


def save_warmup_answers(user_id, username, answers: dict):
    if not answers:
        return
    try:
        sheet = ws("warmup_answers")
        ts    = datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M")
        for key, val in answers.items():
            sheet.append_row([str(user_id), username, key, str(val), ts])
    except Exception as e:
        logger.error(f"save_warmup_answers error: {e}")


def _prefill_anketa_from_warmup(user_id, username, answers):
    try:
        if not answers:
            return
        mapping = {
            "name": "name", "c_name": "name",
            "age": "age",   "c_age": "age",
            "height": "height", "c_height": "height",
            "weight": "weight", "c_weight": "weight",
            "target": "target_weight", "c_target": "target_weight",
            "health": "health", "c_health": "health",
            "experience": "experience", "c_level": "experience",
            "motivation": "motivation", "c_why": "motivation",
        }
        prefill = {}
        for src, dst in mapping.items():
            if answers.get(src) and dst not in prefill:
                prefill[dst] = str(answers[src])
        if not prefill:
            return
        sheet = ws("anketa")
        if str(user_id) in sheet.col_values(1):
            return
        row = [str(user_id), datetime.now(TIMEZONE).strftime("%d.%m.%Y")] + \
              [prefill.get(k, "") for k in ANKETA_KEYS]
        sheet.append_row(row)
        _ws_invalidate("anketa")
    except Exception as e:
        logger.error(f"_prefill_anketa_from_warmup error: {e}")


async def import_warmup_progress(update, context, user):
    if warmup_already_imported(user.id):
        return None
    data = get_warmup_transfer(user.id)
    if not data:
        return None
    xp      = data["xp"]
    coins   = data["coins"]
    answers = data.get("answers", {})
    uname   = uname_of(user)
    name    = get_display_name(user.id, user.first_name)
    try:
        await add_xp(user.id, name, uname, xp, coins, context=context)
    except Exception as e:
        logger.error(f"import_warmup add_xp error {user.id}: {e}")
        return None
    mark_warmup_imported(user.id, xp, coins)
    save_warmup_answers(user.id, uname, answers)
    _prefill_anketa_from_warmup(user.id, uname, answers)
    coins_text = f"🪙 +{coins} монет\n" if coins > 0 else ""
    await safe_send(context, user.id,
                    "🔥 *Твой прогресс из прогрева перенесён!*\n\n"
                    f"⚡ +{xp} XP\n{coins_text}\n"
                    "Тренер тебя уже знает — ответы из прогрева в базе 💪")
    return (xp, coins)


# ── УМНОЕ ПРИВЕТСТВИЕ ─────────────────────────────────────────────────────────
DAY_ABBR = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}


def _smart_greeting(user_id, display_name, p) -> str:
    now   = datetime.now(TIMEZONE)
    hour  = now.hour
    today = now.date()

    if 5 <= hour < 12:
        hi = f"☀️ Доброе утро, {display_name}!"
    elif 12 <= hour < 18:
        hi = f"👋 Привет, {display_name}!"
    elif 18 <= hour < 23:
        hi = f"🌙 Добрый вечер, {display_name}!"
    else:
        hi = f"🦉 Ещё не спишь, {display_name}?"

    ctx = ""
    lw  = p.get("last_workout", "")
    lf  = p.get("last_food", "")
    if lw:
        try:
            days_ago = (today - datetime.strptime(lw, "%Y-%m-%d").date()).days
            if days_ago == 0:
                ctx = "Тренировка сегодня уже есть — отличная работа 💪"
            elif days_ago == 1:
                ctx = "Вчера работал — как восстановление?"
            elif days_ago >= 4:
                ctx = f"Последняя тренировка {days_ago} дня назад — пора возвращаться 🔥"
        except Exception:
            pass
    if not ctx and lf:
        try:
            if (today - datetime.strptime(lf, "%Y-%m-%d").date()).days >= 2:
                ctx = "Питание не заполнял пару дней — не теряй стрик 🥗"
        except Exception:
            pass
    if not ctx:
        sched = get_schedule_days(user_id)
        if sched and DAY_ABBR[today.weekday()] in sched:
            ctx = f"По расписанию сегодня {DAY_ABBR[today.weekday()]} — день тренировки 🏋️"

    return f"{hi}\n_{ctx}_\n\n" if ctx else f"{hi}\n\n"


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        await asyncio.to_thread(register_client, user)
        await import_warmup_progress(update, context, user)
    except Exception as e:
        logger.error(f"start_cmd init error {user.id}: {e}")

    try:
        def _load_start():
            return (get_display_name(user.id, user.first_name),
                    get_progress(user.id)[0],
                    is_anketa_filled(user.id), is_goals_filled(user.id),
                    is_schedule_filled(user.id),
                    get_records_last_date(user.id) is not None)
        (display_name, p, anketa_done, goals_done,
         schedule_done, records_done) = await asyncio.to_thread(_load_start)
        current_xp          = p["xp"]
        current_level, _, _ = get_level_name(current_xp)
        DIV = "━━━━━━━━━━━━━"

        def step(done, text):
            return ("✅ " if done else "⬜ ") + text

        if current_xp < 450:
            text = (
                f"*Твой текущий статус:* «{current_level}»\n"
                "_Не обижайся — так у всех, кто только пришёл._\n"
                "*Вот твой быстрый — СТАРТ\\!*\n\n"
                f"{DIV}\n*🔓 КАК СТАТЬ АДЕПТОМ \\(450 XP\\)*\n{DIV}\n\n"
                + step(anketa_done,   "*Шаг 1\\.* 📝 Анкета → *\\+150 XP \\+100* 🪙\n")
                + step(goals_done,    "*Шаг 2\\.* 🎯 Цели → *\\+100 XP \\+30* 🪙\n")
                + step(schedule_done, "*Шаг 3\\.* 📅 Расписание → *\\+50 XP \\+20* 🪙\n")
                + step(records_done,  "*Шаг 4\\.* 🏆 Рекорды → *\\+30 XP \\+20* 🪙\n")
                + "*Шаг 5\\.* 🏋️ Тренировки *\\(\\+20 XP \\+8* 🪙*\\)*\n"
                "         \\+ 🥗 Питание *\\(\\+10 XP \\+3* 🪙*\\)*\n\n"
                "*📆 Повторяй Шаг 5 всего 7 дней подряд* → до *\\+350 XP* бонусом\\!\n\n"
                f"{DIV}\n*Что получишь:*\n"
                "• 💪 Ранг *«Адепт»*\n"
                "• 🪙 Монеты и опыт\n"
                "• 🔓 Доступ в закрытый канал *Стаи*\n"
                "• 🔥 Уважение братства\n"
                f"{DIV}\n"
            )
            done_count = sum([anketa_done, goals_done, schedule_done, records_done])
            if done_count == 0:
                text += "\n*👇 Нажми кнопку ниже и заполни*\n*📋 Анкету — первым шагом\\!*"
            elif done_count == 4:
                text += "\n_Все шаги выполнены\\! Осталось набирать XP через тренировки\\._ 💪"
            else:
                rem = []
                if not anketa_done:   rem.append("анкету")
                if not goals_done:    rem.append("цели")
                if not schedule_done: rem.append("расписание")
                if not records_done:  rem.append("рекорды")
                text += f"\n_Осталось заполнить: {', '.join(rem)}\\._ 💪"
        else:
            smart_hi = _smart_greeting(user.id, display_name, p)
            text = (
                f"{smart_hi}"
                "Братство уже в движении — присоединяйся\\.\n"
                "Каждый твой день — битва с собой вчерашним\\.\n\n"
                f"📊 *Что приносит силу:*\n"
                f"🏋️ Тренировка → *\\+{XP_PER_WORKOUT} XP \\+{COINS_PER_WORKOUT}* 🪙\n"
                f"🥩 Питание → *\\+{XP_PER_FOOD_DAY} XP \\+{COINS_PER_FOOD_DAY}* 🪙\n"
                f"⚖️ Вес по воскресеньям → *\\+{XP_WEIGH_IN} XP \\+{COINS_WEIGH_IN}* 🪙\n"
                f"🏆 Рекорды → *\\+1 XP за каждый кг*\n\n"
                f"Твой текущий ранг: *{md_safe(current_level)}* \\({current_xp} XP\\)\n"
                "Твой путь: ⚔️ Адепт → 🏋️ Лифтер → 💎 Титан → 🤖 Киборг → 👑 Легенда\n\n"
                "👇 Жми *«📊 Моя статистика»* — и вступай в битву\\.\n"
                "_Братство не спит\\. Братство ждёт твоих побед\\._ 🔥"
            )

        await update.message.reply_text(text, parse_mode="MarkdownV2",
                                        reply_markup=menu_for(user.id))
    except Exception as e:
        logger.error(f"start_cmd error {user.id}: {e}")
        try:
            await update.message.reply_text(
                f"👋 Привет! Добро пожаловать в Стаю.\n\n"
                f"Используй меню внизу 👇",
                reply_markup=menu_for(user.id))
        except Exception:
            pass


# ── СТАТИСТИКА ────────────────────────────────────────────────────────────────
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    _c_del(f"prog_{user.id}")
    try:
        # Чтения таблиц — в поток, иначе бот замирает для всех на время запроса
        def _load_head():
            return (get_progress(user.id)[0], get_total_workouts(user.id),
                    get_display_name(user.id, user.first_name))
        p, total, name = await asyncio.to_thread(_load_head)
        level_name, prev_thr, next_thr = get_level_name(p["xp"])

        # Прогресс-бар
        if next_thr and (next_thr - prev_thr) > 0:
            pct    = int((p["xp"] - prev_thr) / (next_thr - prev_thr) * 100)
            filled = int(16 * pct / 100)
            bar    = "█" * filled + "░" * (16 - filled)
            xp_left = next_thr - p["xp"]
            prog_block = (
                f"`{bar}` {pct}%\n"
                f"До следующего ранга: *{xp_left} XP*"
            )
        else:
            prog_block = "_Максимальный ранг достигнут!_ 👑"

        # Неделя
        try:
            rows      = await asyncio.to_thread(_ws_rows, "workouts", _TTL_SHEET)
            user_rows = [r for r in rows[1:] if len(r) > 1 and r[1] == str(user.id)]
            today     = datetime.now(TIMEZONE).date()
            monday    = today - timedelta(days=today.weekday())
            DAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            week_cells = []
            for i in range(7):
                day     = monday + timedelta(days=i)
                trained = any(r[0].startswith(day.strftime("%d.%m.%Y")) for r in user_rows)
                icon    = "✅" if trained else ("🔲" if day > today else "⬜")
                week_cells.append(f"{DAY_SHORT[i]} {icon}")
            week_line = "  ".join(week_cells)
        except Exception:
            week_line = "нет данных"

        # Вес
        def _load_weight():
            return get_user_current_weight(user.id), get_user_target_weight(user.id)
        cur_w, tgt_w = await asyncio.to_thread(_load_weight)
        if cur_w:
            w_str = f"⚖️ Текущий вес: *{cur_w} кг*"
            if tgt_w:
                diff = round(cur_w - tgt_w, 2)
                sign = "−" if diff > 0 else "+"
                w_str += f"\n🎯 Цель: *{tgt_w} кг*  _{sign}{abs(diff)} кг_"
        elif tgt_w:
            w_str = f"🎯 Цель по весу: *{tgt_w} кг*"
        else:
            w_str = "_Вес пока не записан. Взвесься в воскресенье!_"

        # КБЖУ
        nt     = await asyncio.to_thread(compute_nutrition_targets, user.id)
        nt_block = ""
        if nt:
            nt_block = (
                f"\n━━━━━━━━━━━━━\n"
                f"🍽 *Твоя норма дня*\n"
                f"~{nt['kcal']} ккал  ·  белок *{nt['protein']} г*\n"
                f"жиры {nt['fat']} г  ·  углеводы {nt['carbs']} г\n"
                f"_Режим: {nt['mode']}_"
            )

        # Рейтинг
        rank_block = ""
        try:
            ranked  = await asyncio.to_thread(lambda: _build_ranked(*_read_all_sheets()))
            my_rank = next((i+1 for i, u in enumerate(ranked) if u["id"] == str(user.id)), None)
            if my_rank:
                rank_block = f"\n━━━━━━━━━━━━━\n🏆 *Рейтинг братства:* {my_rank}-е из {len(ranked)}"
                if my_rank > 1:
                    above   = ranked[my_rank - 2]
                    rank_block += f"\n⬆️ До *{md_safe(above['name'])}* — {above['xp'] - p['xp']} XP"
                if my_rank < len(ranked):
                    below   = ranked[my_rank]
                    rank_block += f"\n⬇️ Отрыв от *{md_safe(below['name'])}* — {p['xp'] - below['xp']} XP"
        except Exception:
            pass

        text = (
            f"📊 *ПРОГРЕСС — {md_safe(name)}*\n"
            f"━━━━━━━━━━━━━\n"
            f"👑 {level_name}  ·  *{p['xp']} XP*\n"
            f"{prog_block}\n"
            f"🪙 Монеты: *{p['coins']}*\n"
            f"━━━━━━━━━━━━━\n"
            f"🏋️ *Тренировки*\n"
            f"Всего: *{total}*  ·  Стрик: *{p['streak']} {plural_days(p['streak'])}* (макс {p['max_streak']})\n\n"
            f"🥗 *Питание*\n"
            f"Стрик: *{p['food_streak']} {plural_days(p['food_streak'])}* (макс {p['food_max_streak']})\n\n"
            f"📅 *Эта неделя:*\n"
            f"{week_line}\n"
            f"━━━━━━━━━━━━━\n"
            f"{w_str}"
            f"{nt_block}"
            f"{rank_block}"
        )

        await safe_reply(update, text, reply_markup=menu_for(user.id))
    except Exception as e:
        logger.error(f"stats_cmd error {user.id}: {e}")
        await safe_reply(update, "⚠️ Не удалось загрузить статистику. Попробуй ещё раз.",
                         reply_markup=menu_for(user.id))


# ── ТОП-5 ────────────────────────────────────────────────────────────────────
def _read_all_sheets():
    return (
        _ws_rows_safe("clients",  _TTL_ADMIN_READ),
        _ws_rows_safe("progress", _TTL_ADMIN_READ),
        _ws_rows_safe("workouts", _TTL_ADMIN_READ),
    )


def _build_ranked(clients_rows, progress_rows, workouts_rows):
    prog_map = {}
    for r in progress_rows:
        if r and r[0] and r[0].isdigit():
            try:
                prog_map[r[0]] = int(r[5]) if len(r) > 5 and r[5] else 0
            except Exception:
                pass

    workout_map = {}
    for r in workouts_rows:
        if len(r) > 1 and r[1] and r[1].isdigit():
            workout_map[r[1]] = workout_map.get(r[1], 0) + 1

    ranked = []
    for c in clients_rows:
        if not c or not c[0] or not c[0].isdigit():
            continue
        uid  = c[0]
        xp   = prog_map.get(uid, 0)
        if xp == 0:
            try:
                xp = int(c[9]) if len(c) > 9 and c[9] and c[9].isdigit() else 0
            except Exception:
                pass
        ranked.append({
            "id": uid, "name": display_name_for(c),
            "xp": xp, "workouts": workout_map.get(uid, 0),
            "level": get_level_name(xp)[0],
        })
    ranked.sort(key=lambda x: x["xp"], reverse=True)
    return ranked


async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        ranked = await asyncio.to_thread(lambda: _build_ranked(*_read_all_sheets()))
    except Exception as e:
        logger.error(f"leaderboard load error: {e}")
        await safe_reply(update, "⚠️ Не удалось загрузить рейтинг.", reply_markup=menu_for(user.id))
        return
    if not ranked:
        await safe_reply(update, "💪 В Братстве пока пусто. Стань первым — закрой тренировку!",
                         reply_markup=menu_for(user.id))
        return
    medals = ["🥇", "🥈", "🥉"]
    top = ranked[:100]
    text = "💪 *ТОП-100 БРАТСТВА* 💪\n\n"
    for i, u in enumerate(top):
        medal = medals[i] if i < 3 else f"{i + 1}."
        me = " ← ты" if u["id"] == str(user.id) else ""
        text += f"{medal} *{md_safe(u['name'])}* — {u['level']} · {u['xp']} XP · {u['workouts']} трен.{me}\n"
    text += "\n"
    my_rank = next((i + 1 for i, u in enumerate(ranked) if u["id"] == str(user.id)), None)
    if my_rank:
        text += f"📊 *Твоё место:* {my_rank}-е из {len(ranked)}\n"
    text += "_Братство смотрит. Нападай на первую строчку. 🔥_"
    # Разбиваем длинный текст
    for chunk_start in range(0, len(text), 3800):
        chunk = text[chunk_start:chunk_start + 3800]
        rm = menu_for(user.id) if chunk_start + 3800 >= len(text) else None
        await safe_send(context, update.effective_chat.id, chunk, reply_markup=rm)


async def leaderboard_full_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обратная совместимость — теперь весь ТОП-100 показывается сразу."""
    query = update.callback_query
    await query.answer("Рейтинг уже показан полностью ✅")


# ── БОНУСЫ / ПОДОГРЕВ ────────────────────────────────────────────────────────
async def bonus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    p, _ = await asyncio.to_thread(get_progress, user.id)
    level_name, _, _ = get_level_name(p["xp"])
    xp = p["xp"]

    # Ранги Братства и XP-пороги
    RANK_LIST = [
        (0,     "🫡 Протрузианец"),
        (450,   "⚔️ Адепт"),
        (1500,  "🏋️ Лифтер"),
        (4000,  "💎 Титан"),
        (7500,  "🤖 Киборг"),
        (12000, "👑 Легенда"),
    ]

    FOOTER = (
        "\n━━━━━━━━━━━━━\n"
        "Каждый новый ранг — новые бонусы.\n"
        "Что скрыто за следующими — узнаешь когда дойдёшь. 🔥"
    )

    text = f"🎁 *ПОДОГРЕВ ДЛЯ СВОИХ*\n\n"
    text += f"Твой ранг: *{level_name}* · {xp} XP\n"
    text += "━━━━━━━━━━━━━\n"
    text += "*Путь в Братстве:*\n\n"

    # Динамически строим список рангов
    for threshold, rank_name in RANK_LIST:
        if xp >= threshold:
            text += f"✅ {rank_name} — открыт\n"
        else:
            text += f"🔐 {rank_name} — с {threshold} XP\n"

    text += FOOTER

    # ШОП БАДОВ — доступен ВСЕМ (с Протрузианца)
    text += (
        "\n\n━━━━━━━━━━━━━\n"
        "💊 *ШОП БАДОВ*\n\n"
        "🔥 *Скидка 10%* на первый заказ от 10 000 ₽\n"
        "Качественные добавки для результата 👇"
    )

    buttons = [
        [InlineKeyboardButton("🛒 Заказать в приложении", url=SUPPLEMENT_SHOP_URL)],
        [InlineKeyboardButton("📲 Заказать у менеджера",  url=MANAGER_URL)],
    ]

    # Магазин монет — с ранга Лифтер (1500+ XP)
    if xp >= 1500:
        text += (
            "\n\n━━━━━━━━━━━━━\n"
            "🪙 *МАГАЗИН БРАТСТВА*\n\n"
            f"У тебя: *{p['coins']} монет* 🎁\n\n"
            "Трать монеты на реальные бонусы 👇"
        )
        buttons.append([InlineKeyboardButton("🏋️ Онлайн-тренировка 1ч — 700 🪙",
                                              callback_data="shop_training")])
        buttons.append([InlineKeyboardButton("💬 Личная консультация 40 мин — 500 🪙",
                                              callback_data="shop_consult")])

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ── РАСПИСАНИЕ ────────────────────────────────────────────────────────────────
async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    already = is_schedule_filled(user.id)
    schedule_states[user.id] = {
        "step": 0, "answers": {}, "award": not already,
        "first_name": user.first_name or "", "username": uname_of(user),
    }
    save_states()
    if already:
        await safe_reply(update,
                         "✏️ Обновим расписание. *XP за это уже начислены* — просто перезапишем данные.")
    await ask_schedule_question(update.effective_chat.id, user.id, context)


def _day_options_for(days_count):
    if days_count <= 2:
        return ["Пн, Чт", "Вт, Пт", "Ср, Сб", "Пн, Пт", "Гибко — сам выберу"]
    elif days_count == 3:
        return ["Пн, Ср, Пт", "Вт, Чт, Сб", "Пн, Ср, Сб", "Гибко — сам выберу"]
    elif days_count == 4:
        return ["Пн, Вт, Чт, Пт", "Пн, Ср, Пт, Сб", "Вт, Чт, Сб, Вс", "Гибко — сам выберу"]
    else:
        return ["Пн-Пт", "Пн, Вт, Ср, Чт, Пт", "Пн, Вт, Ср, Пт, Сб", "Гибко — сам выберу"]


async def ask_schedule_question(chat_id, user_id, context):
    if user_id not in schedule_states:
        return
    step = schedule_states[user_id]["step"]
    if step >= len(SCHEDULE_QUESTIONS):
        await finish_schedule(chat_id, user_id, context)
        return
    if step == 1:
        q_text  = "💪 В какие дни тренируешься?"
        options = schedule_states[user_id].get("day_options") or SCHEDULE_QUESTIONS[1][1]
    else:
        q_text, options = SCHEDULE_QUESTIONS[step]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📅 *Вопрос {step + 1}/{len(SCHEDULE_QUESTIONS)}:*\n\n{q_text}",
        parse_mode="Markdown",
        reply_markup=wide_keyboard(options, f"sched_{step}"),
    )


async def schedule_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    if user.id not in schedule_states:
        await query.edit_message_text("Настройка не активна. Нажми «Расписание» заново.")
        return
    parts = query.data.split("_")
    step  = int(parts[1]); opt = int(parts[2])
    if step == 1:
        options = schedule_states[user.id].get("day_options") or SCHEDULE_QUESTIONS[1][1]
        answer  = options[opt]
    else:
        answer = SCHEDULE_QUESTIONS[step][1][opt]
    schedule_states[user.id]["answers"][f"q{step}"] = answer
    if step == 0:
        dc = parse_kg(answer) or 3
        schedule_states[user.id]["days_count"]  = dc
        schedule_states[user.id]["day_options"] = _day_options_for(dc)
    schedule_states[user.id]["step"] = step + 1
    await query.edit_message_text(f"✅ {answer}")
    await ask_schedule_question(update.effective_chat.id, user.id, context)


async def finish_schedule(chat_id, user_id, context):
    state   = schedule_states[user_id]
    answers = state["answers"]
    award   = state.get("award", True)
    try:
        sheet = ws("clients")
        ids   = sheet.col_values(1)
        if str(user_id) in ids:
            idx = ids.index(str(user_id)) + 1
            sheet.update_cell(idx, 5, answers.get("q0", ""))
            sheet.update_cell(idx, 6, answers.get("q1", ""))
            sheet.update_cell(idx, 7, f"{answers.get('q2', '')} / {answers.get('q3', '')}")
            _invalidate_client_row(user_id)
    except Exception as e:
        logger.error(f"finish_schedule write error: {e}")
    xp_line = ""
    if award:
        await add_xp(user_id, state.get("first_name", ""), state.get("username", ""),
                     XP_SCHEDULE_BONUS, coins_amount=COINS_SCHEDULE, context=context)
        mark_schedule_filled(user_id)
        xp_line = f"  +{XP_SCHEDULE_BONUS} XP  •  🪙 +{COINS_SCHEDULE}"
    await safe_send(context, chat_id,
                    f"✅ *Расписание сохранено!*{xp_line}\n"
                    f"━━━━━━━━━━━━━\n"
                    f"📆 *{answers.get('q0', '—')}* в неделю\n"
                    f"📅 Дни: *{answers.get('q1', '—')}*\n"
                    f"🕐 Время: *{answers.get('q2', '—')}*\n"
                    f"⏱️ Длина: *{answers.get('q3', '—')}*\n"
                    f"━━━━━━━━━━━━━\n"
                    "_В тренировочные дни пришлю напоминание утром._\n"
                    "_В дни отдыха — напомню про питание._ 🥗",
                    reply_markup=menu_for(user_id))
    del schedule_states[user_id]
    save_states()


# ── ЦЕЛИ ─────────────────────────────────────────────────────────────────────
async def goals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    already = is_goals_filled(user.id)
    goals_test_states[user.id] = {
        "step": 0, "answers": [], "award": not already,
        "first_name": user.first_name or "", "username": uname_of(user),
    }
    save_states()
    if already:
        await safe_reply(update,
                         "✏️ Обновим цели. *XP за это уже начислены* — просто перезапишем приоритеты.")
    await ask_goals_question(update.effective_chat.id, user.id, context)


async def ask_goals_question(chat_id, user_id, context):
    if user_id not in goals_test_states:
        return
    step = goals_test_states[user_id]["step"]
    if step >= len(GOALS_TEST):
        await finish_goals_test(chat_id, user_id, context)
        return
    q = GOALS_TEST[step]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎯 *Вопрос {step + 1}/{len(GOALS_TEST)}:*\n\n{q['q']}",
        parse_mode="Markdown",
        reply_markup=wide_keyboard(q["options"], f"goal_{step}"),
    )


async def goals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    if user.id not in goals_test_states:
        await query.edit_message_text("Тест не активен. Нажми «Цели» заново.")
        return
    parts  = query.data.split("_")
    step   = int(parts[1]); opt = int(parts[2])
    answer = GOALS_TEST[step]["options"][opt]
    goals_test_states[user.id]["answers"].append({"q": GOALS_TEST[step]["q"], "a": answer})
    goals_test_states[user.id]["step"] = step + 1
    await query.edit_message_text(f"✅ {answer}")
    await ask_goals_question(update.effective_chat.id, user.id, context)


async def finish_goals_test(chat_id, user_id, context):
    state   = goals_test_states[user_id]
    answers = state["answers"]
    award   = state.get("award", True)
    try:
        sheet = ws("goals")
        ids   = sheet.col_values(1)
        row   = [str(user_id), datetime.now(TIMEZONE).strftime("%d.%m.%Y")] + \
                [a["a"] for a in answers]
        if str(user_id) in ids:
            idx = ids.index(str(user_id)) + 1
            for ci, val in enumerate(row, start=1):
                sheet.update_cell(idx, ci, val)
        else:
            sheet.append_row(row)
        _ws_invalidate("goals")
    except Exception:
        pass
    xp_line = ""
    if award:
        mark_goals_filled(user_id)
        await add_xp(user_id, state.get("first_name", ""), state.get("username", ""),
                     XP_GOALS_BONUS, coins_amount=COINS_GOALS, context=context)
        xp_line = f"  +{XP_GOALS_BONUS} XP  •  🪙 +{COINS_GOALS}"
    primary  = answers[0]["a"] if answers else "—"
    sec      = answers[1]["a"] if len(answers) > 1 else "—"
    coaching = answers[7]["a"] if len(answers) > 7 else "—"
    await safe_send(context, chat_id,
                    f"🎯 *Цели сохранены!*{xp_line}\n\n"
                    f"🥇 Приоритет 1: *{primary}*\n"
                    f"🥈 Приоритет 2: *{sec}*\n"
                    f"🤝 Стиль работы: *{coaching}*\n\n"
                    "Тренер учтёт приоритеты при составлении программы. 💪",
                    reply_markup=menu_for(user_id))
    del goals_test_states[user_id]
    save_states()


# ── АНКЕТА ────────────────────────────────────────────────────────────────────
async def anketa_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_anketa_filled(user.id):
        await safe_reply(update, "✅ Анкета уже заполнена! +150 XP уже начислены.",
                         reply_markup=menu_for(user.id))
        return
    anketa_states[user.id] = {
        "step": 0, "answers": {},
        "first_name": user.first_name or "", "username": uname_of(user),
    }
    save_states()
    await ask_anketa_question(update.effective_chat.id, user.id, context)


async def ask_anketa_question(chat_id, user_id, context):
    if user_id not in anketa_states:
        return
    step = anketa_states[user_id]["step"]
    if step >= len(ANKETA):
        await finish_anketa(chat_id, user_id, context)
        return
    key, q_type, q_text, options = ANKETA[step]
    if q_type == "choice":
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📋 *Вопрос {step + 1}/{len(ANKETA)}:*\n\n{q_text}",
            parse_mode="Markdown",
            reply_markup=wide_keyboard(options, f"ank_{step}"),
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📋 *Вопрос {step + 1}/{len(ANKETA)}:*\n\n{q_text}",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        anketa_states[user_id]["waiting_text"] = True


async def anketa_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    if user.id not in anketa_states:
        await query.edit_message_text("Анкета не активна. Нажми «Анкета» заново.")
        return
    parts  = query.data.split("_")
    step   = int(parts[1]); opt = int(parts[2])
    key, q_type, q_text, options = ANKETA[step]
    anketa_states[user.id]["answers"][key] = options[opt]
    anketa_states[user.id]["step"]         = step + 1
    await query.edit_message_text(f"✅ {options[opt]}")
    await ask_anketa_question(update.effective_chat.id, user.id, context)


async def finish_anketa(chat_id, user_id, context):
    state   = anketa_states.get(user_id, {})
    answers = state.get("answers", {})
    try:
        sheet = ws("anketa")
        ids   = sheet.col_values(1)
        row   = [str(user_id), datetime.now(TIMEZONE).strftime("%d.%m.%Y")] + \
                [answers.get(k, "") for k in ANKETA_KEYS]
        if str(user_id) in ids:
            idx = ids.index(str(user_id)) + 1
            for ci, val in enumerate(row, start=1):
                sheet.update_cell(idx, ci, val)
        else:
            sheet.append_row(row)
        _ws_invalidate("anketa")
    except Exception:
        pass
    mark_anketa_filled(user_id)
    fallback = state.get("first_name", "")
    await add_xp(user_id, fallback, state.get("username", ""),
                 XP_ANKETA_BONUS, coins_amount=COINS_ANKETA, context=context)
    name = answers.get("name", fallback)

    # Персональные нормы сразу после анкеты
    nt      = compute_nutrition_targets(user_id)
    nt_text = ""
    if nt:
        nt_text = (
            f"\n\n━━━━━━━━━━━━━\n"
            f"🍽 *Твоя персональная норма КБЖУ:*\n"
            f"• Калории: ~*{nt['kcal']} ккал*\n"
            f"• Белок: *{nt['protein']} г*\n"
            f"• Жиры: {nt['fat']} г  ·  Углеводы: {nt['carbs']} г\n"
            f"• Режим: _{nt['mode']}_\n\n"
            "_Это расчёт по твоим данным — ориентир, не догма._"
        )

    await safe_send(context, chat_id,
                    f"✅ *{md_safe(name)}, анкета заполнена!*\n\n"
                    f"⭐ *+{XP_ANKETA_BONUS} XP*  ·  🪙 *+{COINS_ANKETA}*\n\n"
                    "Данные у тренера. Программа составляется с учётом твоих ответов. 💪"
                    f"{nt_text}\n\n"
                    "_Кнопка «Анкета» скрыта — всё заполнено._",
                    reply_markup=menu_for(user_id))
    del anketa_states[user_id]
    save_states()


# ── ТРЕНИРОВКИ ────────────────────────────────────────────────────────────────
def has_workout_on(user_id, d):
    dstr = d.strftime("%d.%m.%Y")
    try:
        rows = _ws_rows("workouts", _TTL_SHEET)
        for r in rows[1:]:
            if len(r) > 1 and r[1] == str(user_id) and r[0].startswith(dstr):
                return True
    except Exception:
        pass
    return False


# ── ТЕКСТ ПОДСКАЗКИ ОТЧЁТА О ТРЕНИРОВКЕ ─────────────────────────────────────
WORKOUT_REPORT_HINT = (
    "🔥 *Расскажи о тренировке*\n\n"
    "Зачем: зафиксируем прогресс, я сравню с прошлым разом, "
    f"получишь *+{XP_PER_WORKOUT} XP* 🪙\n\n"
    "*Формат:*\n"
    "• По плану тренера — скинь его с выполненными весами\n"
    "• Своя тренировка — опиши свободно\n\n"
    "*Пример:*\n"
    "«Присед 4х5х100, жим 4х8х70,\n"
    "тяга 3х5х120»\n\n"
    "👇 Пиши внизу — я всё запишу."
)


async def workout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Все чтения таблиц — одним пакетом в потоке, чтобы не блокировать бота
    def _load():
        p     = get_progress(user.id)[0]
        total = get_total_workouts(user.id)
        name  = get_display_name(user.id, user.first_name)
        cl    = next((c for c in get_all_clients() if c and c[0] == str(user.id)), None)
        return p, total, name, (planned_workouts_per_week(cl) if cl else 0)

    try:
        p, total, name, planned = await asyncio.to_thread(_load)
    except Exception as e:
        logger.error(f"workout_cmd load error {user.id}: {e}")
        p, total, name, planned = dict(PROGRESS_EMPTY), 0, (user.first_name or "Боец"), 0

    plan_line = (f"Закрой *{planned}* {plural(planned, 'тренировку', 'тренировки', 'тренировок')} за неделю"
                 if planned else "Закрой недельный план")

    summary = (
        f"🏋️ *ТРЕНИРОВКИ*\n"
        f"━━━━━━━━━━━━━\n"
        f"*{md_safe(name)}* — *{total}* {plural(total, 'тренировка', 'тренировки', 'тренировок')} всего\n"
        f"🔥 Стрик: *{p['streak']} {plural_days(p['streak'])}* (лучший: {p['max_streak']})\n\n"
        f"🎁 {plan_line} → *+{XP_STREAK_WEEK} XP +{COINS_STREAK_WEEK}* 🪙\n"
        f"━━━━━━━━━━━━━\n\n"
        "За какой день отчёт?\n"
        "_Вчера не успел — закрой сейчас, стрик сохранится._"
    )
    await safe_reply(update, summary,
                     reply_markup=InlineKeyboardMarkup([
                         [InlineKeyboardButton("📝 Отчёт за сегодня", callback_data="wk_today")],
                         [InlineKeyboardButton("📅 Отчёт за вчера",   callback_data="wk_yesterday")],
                     ]))


async def workout_day_callback(update: Update, context: ContextTypes.DEFAULT_TYPE,
                               day: str = None, skip_wellbeing: bool = False):
    """Приём отчёта о тренировке.

    day: "today" | "yesterday" — передаётся роутером явно.
         Объекты Telegram в PTB v20+ неизменяемы, поэтому подменять
         query.data НЕЛЬЗЯ — раньше здесь падал AttributeError и кнопка
         просто крутилась вечно.
    skip_wellbeing: True, если пользователь пришёл сразу после опроса.
    """
    query = update.callback_query
    await query.answer()
    user  = query.from_user

    if day is None:
        day = "yesterday" if (query.data or "").endswith("yesterday") else "today"
    today  = datetime.now(TIMEZONE).date()
    target = today if day == "today" else today - timedelta(days=1)
    target_disp = target.strftime("%d.%m.%Y")

    try:
        if day == "today":
            def _check_today():
                p = get_progress(user.id)[0]
                return (p["last_workout"] == today.strftime("%Y-%m-%d")
                        or has_workout_on(user.id, today))

            already = await asyncio.to_thread(_check_today)
            if already:
                await query.edit_message_text(
                    "✅ *Ты уже закрыл тренировку сегодня!*\n\n"
                    "XP уже начислены. Восстановление — тоже часть работы.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("📝 Внести правки", callback_data="workout_append")
                    ]]),
                )
                return

            # ── Гейт самочувствия ────────────────────────────────────────────
            # Опрос нужен ТОЛЬКО если он ещё не пройден сегодня.
            # Раньше условие было перевёрнуто (`not in wellbeing_states`),
            # из-за чего после опроса он запускался снова и снова.
            if not skip_wellbeing:
                if user.id in wellbeing_states:
                    # Опрос уже идёт — просто продолжаем его
                    await query.edit_message_text("💚 Сначала закончим про самочувствие 👇")
                    await ask_wellbeing_question(user.id, user.id, context)
                    return
                if not await _wellbeing_done_today(user.id):
                    await query.edit_message_text("💚 Сначала пара слов о самочувствии 👇")
                    await start_wellbeing_survey(user.id, user.id, context,
                                                 user.first_name or "", uname_of(user))
                    return
        else:
            if await asyncio.to_thread(has_workout_on, user.id, target):
                await query.edit_message_text(
                    f"✅ Отчёт за *{target_disp}* уже принят — стрик в порядке. 💪",
                    parse_mode="Markdown",
                )
                await safe_send(context, user.id, "Главное меню:",
                                reply_markup=menu_for(user.id))
                return
    except Exception as e:
        # Проверки не должны мешать человеку сдать отчёт
        logger.error(f"workout_day_callback checks {user.id}: {e}")

    user_states[user.id] = {"mode": "workout", "for_date": target_disp}
    save_states()

    try:
        await query.edit_message_text(f"🏋️ Принимаю отчёт за *{target_disp}*",
                                      parse_mode="Markdown")
    except Exception:
        pass
    # Подробная инструкция — отдельным сообщением
    await safe_send(context, user.id, WORKOUT_REPORT_HINT, reply_markup=cancel_keyboard())


async def workout_append_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    user_states[user.id] = {"mode": "workout_append"}
    save_states()
    await query.edit_message_text(
        "📝 Напиши что хочешь добавить к сегодняшней тренировке "
        "(детали, веса, ощущения). XP повторно не начисляются."
    )


# ── ПИТАНИЕ ───────────────────────────────────────────────────────────────────
def food_reported_today(user_id) -> bool:
    today_str = datetime.now(TIMEZONE).strftime("%d.%m.%Y")
    try:
        rows = _ws_rows("food_log", _TTL_SHEET)
        for r in (rows[1:] if rows else []):
            if len(r) > 2 and r[0] == str(user_id) and r[2] == today_str:
                return True
    except Exception:
        pass
    return False


def get_food_today_entry(user_id) -> dict:
    today_str = datetime.now(TIMEZONE).strftime("%d.%m.%Y")
    try:
        rows = _ws_rows("food_log", _TTL_SHEET)
        for r in (rows[1:] if rows else []):
            if len(r) > 2 and r[0] == str(user_id) and r[2] == today_str:
                return {"text": r[3] if len(r) > 3 else "",
                        "kbju": r[4] if len(r) > 4 else ""}
    except Exception:
        pass
    return {}


def update_food_today_entry(user_id, username, new_text: str, new_kbju: str):
    today_str = datetime.now(TIMEZONE).strftime("%d.%m.%Y")
    try:
        sheet = ws("food_log")
        rows  = sheet.get_all_values()
        for i, r in enumerate(rows, start=1):
            if len(r) > 2 and r[0] == str(user_id) and r[2] == today_str:
                sheet.update_cell(i, 4, new_text[:3000])
                sheet.update_cell(i, 5, new_kbju[:1500])
                _ws_invalidate("food_log")
                return True
        sheet.append_row([str(user_id), username, today_str,
                          new_text[:1000], new_kbju[:500]])
        _ws_invalidate("food_log")
        return True
    except Exception as e:
        logger.error(f"update_food_today_entry error: {e}")
        return False


def get_workout_today_entry(user_id) -> dict:
    """Возвращает запись тренировки за сегодня или пустой dict."""
    today_str = datetime.now(TIMEZONE).strftime("%d.%m.%Y")
    try:
        rows = _ws_rows("workouts", _TTL_SHEET)
        for r in (rows[1:] if rows else []):
            if len(r) > 1 and r[1] == str(user_id) and r[0].startswith(today_str):
                return {
                    "assessment":  r[3] if len(r) > 3 else "",
                    "workout_log": r[4] if len(r) > 4 else "",
                    "user_text":   r[5] if len(r) > 5 else "",
                }
    except Exception:
        pass
    return {}


async def food_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Весь блок чтений — одним заходом в поток
    def _load():
        already = food_reported_today(user.id)
        return (get_progress(user.id)[0],
                get_display_name(user.id, user.first_name),
                count_food_days(user.id),
                get_user_current_weight(user.id),
                get_user_target_weight(user.id),
                compute_nutrition_targets(user.id),
                already,
                get_food_today_entry(user.id) if already else {})

    try:
        (p, name, food_days, cur_w, tgt_w, nt,
         already_today, today_entry) = await asyncio.to_thread(_load)
    except Exception as e:
        logger.error(f"food_cmd load error {user.id}: {e}")
        await safe_reply(update,
                         "⚠️ Не смог получить данные — попробуй ещё раз через минуту.",
                         reply_markup=menu_for(user.id))
        return

    cur_line   = f"{cur_w} кг" if cur_w else "—"
    tgt_line   = f"{tgt_w} кг" if tgt_w else "—"
    delta_line = ""
    if cur_w and tgt_w:
        diff = cur_w - tgt_w
        if diff > 0:
            delta_line = f"  _(до цели: −{diff} кг)_"
        elif diff < 0:
            delta_line = f"  _(до цели: +{abs(diff)} кг)_"
        else:
            delta_line = "  _🎯 цель достигнута!_"

    nt_line = ""
    if nt:
        nt_line = (
            f"\n🍽 *Норма:* ~{nt['kcal']} ккал  ·  белок *{nt['protein']} г*"
            f"  ·  _режим: {nt['mode']}_"
        )

    summary = (
        f"🥗 *ПИТАНИЕ*\n"
        f"━━━━━━━━━━━━━\n"
        f"*{md_safe(name)}* — {food_days} дней с отчётом\n"
        f"🔥 Стрик: *{p['food_streak']} {plural_days(p['food_streak'])}* (лучший: {p['food_max_streak']})\n"
        f"⚖️ Вес: *{cur_line}*{delta_line}  →  Цель: *{tgt_line}*"
        f"{nt_line}\n\n"
        f"🎁 7 дней подряд → *+{XP_STREAK_WEEK} XP +{COINS_STREAK_WEEK}* 🪙\n"
        f"━━━━━━━━━━━━━\n\n"
    )

    if already_today:
        existing_text = today_entry.get("text", "")
        existing_kbju = today_entry.get("kbju", "")
        preview       = existing_text[:200] + "..." if len(existing_text) > 200 else existing_text
        food_states[user.id] = {
            "mode": "supplement", "meals": [existing_text] if existing_text else [],
            "photos": [], "existing_text": existing_text,
        }
        msg = (summary + "✅ *Отчёт за сегодня уже есть.*\n\n")
        if preview:
            msg += f"_{preview}_\n\n"
        if existing_kbju:
            msg += f"📊 {existing_kbju[:150]}\n\n"
        msg += ("Хочешь *дополнить рацион*?\n"
                "Скинь что ещё ел — фото или текст. Пересчитаю КБЖУ за весь день. 🔄\n\n"
                "_Или нажми Отмена._")
        await update.message.reply_text(
            msg, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Дополнить рацион",    callback_data="food_supplement")],
                [InlineKeyboardButton("❌ Всё, рацион полный", callback_data="food_cancel")],
            ]),
        )
    else:
        food_states[user.id] = {"mode": "collecting", "meals": [], "photos": []}
        msg = (
            summary +
            "*Отчёт по питанию* 🥗\n\n"
            "Отправь весь свой рацион!\n"
            "За вчера или сегодня…\n\n"
            "Что принимается:\n"
            "📸 До 5 фото еды (завтрак, обед, ужин, перекусы)\n"
            "📝 Текст — что и сколько съел\n"
            "📸+📝 Фото с подписями — идеально\n\n"
            "_Всё что пришлёшь — объединится в один анализ КБЖУ за день._\n\n"
            "Когда добавишь всё — жми *«Готово»*."
        )
        await update.message.reply_text(
            msg, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово — анализируй!", callback_data="food_done")],
                [InlineKeyboardButton("❌ Отмена",               callback_data="food_cancel")],
            ]),
        )


async def food_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user  = query.from_user

    if query.data == "food_cancel":
        food_states.pop(user.id, None)
        await query.edit_message_text("❌ Отменено.")
        await safe_send(context, user.id, "Главное меню:", reply_markup=menu_for(user.id))
        return

    # Запуск питания из блока восстановления
    if query.data == "food_done_init":
        food_states[user.id] = {"mode": "collecting", "meals": [], "photos": []}
        await query.edit_message_text(
            "🥗 *Отчёт по питанию*\n\n"
            "Скинь фото или напиши что ел сегодня.\n"
            "Посмотрим где можно добавить восстановлению.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово — анализируй!", callback_data="food_done")],
                [InlineKeyboardButton("❌ Отмена",               callback_data="food_cancel")],
            ]),
        )
        return

    if query.data == "food_skip_recovery":
        # ⚠️ edit_message_text принимает ТОЛЬКО inline-клавиатуру.
        # Раньше сюда передавался menu_for() (ReplyKeyboardMarkup) —
        # Telegram отвечал ошибкой, и кнопка «не работала».
        await query.edit_message_text(
            "Понял 👊 Держи темп, но слушай тело.\n\n"
            "👇 *«🏋️ Тренировки»* — когда будешь готов.",
            parse_mode="Markdown",
        )
        await safe_send(context, user.id, "Главное меню:",
                        reply_markup=menu_for(user.id))
        return

    if query.data == "food_supplement":
        if user.id in food_states:
            food_states[user.id]["mode"] = "collecting"
        await query.edit_message_text(
            "📸 Добавь что ещё ел — фото или текст.\nКогда всё — жми *«Готово»*.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово — пересчитай!", callback_data="food_done")],
                [InlineKeyboardButton("❌ Отмена",               callback_data="food_cancel")],
            ]),
        )
        return

    if user.id not in food_states:
        await query.edit_message_text("Сессия не найдена. Нажми «Питание» заново.")
        return

    # Защита от двойного нажатия
    if user.id in _processing:
        await query.answer("⏳ Уже обрабатываю...")
        return
    _processing.add(user.id)
    try:

        state         = food_states[user.id]
        is_supplement = state.get("mode") == "supplement" or "existing_text" in state
        meals_text    = "\n".join(state.get("meals", []))
        photos_b64    = state.get("photos", [])

        if not meals_text and not photos_b64:
            await query.answer("Сначала напиши что ел или пришли фото!", show_alert=True)
            return

        # Rate limit
        ok, wait_sec = _ai_rate_ok(user.id)
        if not ok:
            await query.answer(f"⏳ Подожди {wait_sec} сек. перед следующим анализом.", show_alert=True)
            return
        _ai_mark(user.id)

        await query.edit_message_text("⏳ Анализирую питание за весь день...")

        existing_text = state.get("existing_text", "")
        if is_supplement and existing_text:
            full_text = f"{existing_text}\nДОПОЛНЕНИЕ:\n{meals_text}" if meals_text else existing_text
        else:
            full_text = meals_text

        def _load_food_ctx():
            return compute_nutrition_targets(user.id), build_coach_context(user.id)
        try:
            targets, coach_ctx = await asyncio.to_thread(_load_food_ctx)
        except Exception as e:
            logger.error(f"food ctx load {user.id}: {e}")
            targets, coach_ctx = None, ""

        # Тяжёлый вызов Claude — через to_thread чтобы не блокировать event loop
        analysis = await asyncio.to_thread(
            analyze_food_report, full_text, photos_b64 or None, targets, coach_ctx
        )

        today_str    = datetime.now(TIMEZONE).strftime("%d.%m.%Y")
        kbju_summary = extract_food_summary(analysis) if analysis else ""

        # Для фото-отчётов full_text пуст — берём сводку из анализа Claude
        sheet_meals = full_text.strip() if full_text.strip() else kbju_summary
        photo_note = f"[📸 {len(photos_b64)} фото] " if (photos_b64 and not full_text.strip()) else ""
        sheet_col_d = f"{photo_note}{sheet_meals}"[:3000]

        if is_supplement:
            await asyncio.to_thread(update_food_today_entry, user.id, uname_of(user),
                                    sheet_col_d, kbju_summary)
            xp_note = ""
        else:
            try:
                await asyncio.to_thread(
                    lambda: ws("food_log").append_row([
                        str(user.id), uname_of(user), today_str,
                        sheet_col_d,
                        kbju_summary[:1500],
                    ])
                )
                _ws_invalidate("food_log")
            except Exception as e:
                logger.error(f"food_log append error: {e}")

            # Уведомление тренеру — ПОЛНАЯ сводка с блюдами и КБЖУ
            try:
                display = get_display_name(user.id, user.first_name)
                trainer_food = (analysis or kbju_summary or "нет данных")[:800]
                await _notify_trainer(context, user.id, display,
                    f"🥗 *Питание сдано!*\n\n"
                    f"{trainer_food}")
            except Exception:
                pass
            await add_xp(user.id, user.first_name or "", uname_of(user),
                         XP_PER_FOOD_DAY, coins_amount=COINS_PER_FOOD_DAY, context=context)
            fstreak = await update_food_progress(user.id, user.first_name or "", uname_of(user), context)
            p_after, _ = get_progress(user.id)
            streak_line = (f"🔥 Стрик питания: *{fstreak} {plural_days(fstreak)}*"
                           + (" 🏅" if fstreak >= 7 else " — не останавливайся!" if fstreak >= 3 else ""))
            xp_note = (
                f"━━━━━━━━━━━━━\n"
                f"⭐ *+{XP_PER_FOOD_DAY} XP*  ·  🪙 *+{COINS_PER_FOOD_DAY}*\n"
                f"{streak_line}\n"
                f"💎 Всего XP: *{p_after['xp']}*\n"
                f"━━━━━━━━━━━━━\n\n"
            )
            await check_food_streak_bonus(user.id, user.first_name or "", uname_of(user), context)

        header = ("🔄 *Пересчитано — рацион за весь день:*\n\n"
                  if is_supplement else f"🥗 *Питание засчитано!*\n{xp_note}")
        await safe_send(context, user.id, f"{header}{analysis}",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("Спасибо, понял 🙏",
                                                 callback_data="feedback_ack"),
                        ]]))
        del food_states[user.id]
        _processing.discard(user.id)
    except Exception as e:
        _processing.discard(user.id)
        logger.error(f"food processing error {user.id}: {e}")
        food_states.pop(user.id, None)
        await safe_send(context, user.id, "⚠️ Ошибка при анализе. Попробуй ещё раз.",
                        reply_markup=menu_for(user.id))


# ── ВЗВЕШИВАНИЕ ───────────────────────────────────────────────────────────────
def weighed_this_week(user_id):
    try:
        rows       = _ws_rows("weight_log", _TTL_SHEET)
        today      = datetime.now(TIMEZONE).date()
        week_start = today - timedelta(days=today.weekday())
        for r in rows[1:]:
            if len(r) > 1 and r[0] == str(user_id):
                try:
                    d = datetime.strptime(r[1], "%d.%m.%Y").date()
                    if d >= week_start:
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


async def weighin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    today = datetime.now(TIMEZONE)
    if today.weekday() != 6:
        days_left = (6 - today.weekday()) % 7
        tgt_w = await asyncio.to_thread(get_user_target_weight, user.id)
        tgt_line = f"\n\n🎯 Текущая цель: *{tgt_w} кг*" if tgt_w else ""
        await safe_reply(update,
                         "⚖️ *Взвешивание — только по воскресеньям!*\n\n"
                         "В воскресенье — в любое время дня.\n"
                         "_Рекомендуется утром натощак, до еды и воды._\n\n"
                         f"Ближайшее взвешивание\n— через *{days_left} {plural_days(days_left)}*!"
                         f"{tgt_line}",
                         reply_markup=InlineKeyboardMarkup([[
                             InlineKeyboardButton("🎯 Указать желаемый вес",
                                                  callback_data="weighin_set_target"),
                         ]]))
        return
    if await asyncio.to_thread(weighed_this_week, user.id):
        await safe_reply(update,
                         "✅ Ты уже взвесился сегодня!\n"
                         "Следующее — в следующее воскресенье. ⚖️",
                         reply_markup=InlineKeyboardMarkup([[
                             InlineKeyboardButton("🎯 Изменить цель по весу",
                                                  callback_data="weighin_set_target"),
                         ]]))
        return
    weighin_states[user.id] = {"mode": "waiting"}
    save_states()
    cur_w = get_user_current_weight(user.id)
    tgt_w = get_user_target_weight(user.id)
    context_line = ""
    if cur_w and tgt_w:
        diff = round(abs(cur_w - tgt_w), 2)
        direction = "осталось" if cur_w > tgt_w else "набрать"
        context_line = (f"\n_Прошлый результат: *{cur_w} кг*  ·  "
                        f"Цель: *{tgt_w} кг*  ·  {direction} {diff} кг_\n")
    elif cur_w:
        context_line = f"\n_Прошлый результат: *{cur_w} кг*_\n"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎯 Указать желаемый вес", callback_data="weighin_set_target"),
        InlineKeyboardButton("❌ Отмена",               callback_data="weighin_cancel"),
    ]])
    await safe_reply(update,
                     f"⚖️ *Измерь вес*\n"
                     f"━━━━━━━━━━━━━\n"
                     f"{context_line}\n"
                     "Напиши цифру — принимаю с десятичными.\n"
                     "_Например: 82.35 или 82,35_\n\n"
                     "Или пришли 📸 фото весов.\n\n"
                     f"*+{XP_WEIGH_IN} XP*  ·  🪙 *+{COINS_WEIGH_IN}*",
                     reply_markup=kb)


async def save_weight(user_id, weight_value, context, photo_note="",
                      first_name="", username=""):
    try:
        await asyncio.to_thread(
            lambda: (
                ws("weight_log").append_row([
                    str(user_id),
                    datetime.now(TIMEZONE).strftime("%d.%m.%Y"),
                    str(weight_value), photo_note,
                ]),
                _ws_invalidate("weight_log"),
            )
        )
    except Exception:
        pass

    await add_xp(user_id, first_name or "", username or "",
                 XP_WEIGH_IN, coins_amount=COINS_WEIGH_IN, context=context)

    trend = ""
    try:
        rows = _ws_rows("weight_log", _TTL_SHEET)
        nums = [parse_weight_float(r[2]) for r in rows[1:]
                if len(r) > 2 and r[0] == str(user_id) and parse_weight_float(r[2]) > 0]
        if len(nums) >= 2:
            diff = round(nums[-1] - nums[-2], 2)
            if diff > 0:
                trend = f"\n📈 С прошлого раза: +{diff} кг"
            elif diff < 0:
                trend = f"\n📉 С прошлого раза: {diff} кг"
            else:
                trend = "\n➡️ Вес без изменений"
    except Exception:
        pass

    weight_comment = ""
    if weight_value and isinstance(weight_value, (int, float)) and weight_value > 0:
        target = get_user_target_weight(user_id)
        hist   = get_user_weight_history(user_id, weeks=20)
        if target and hist and len(hist) >= 2:
            diff   = weight_value - hist[-2][1]
            to_g   = weight_value - target
            if diff < 0:
                weight_comment = (f"\n📉 −{abs(diff)} кг с прошлой недели. "
                                  f"До цели: {to_g} кг — идёшь верно 💪" if to_g > 0
                                  else "\n🎯 Цель достигнута! 🏆")
            elif diff > 0:
                weight_comment = (f"\n📈 +{diff} кг. Следим за питанием 🥗"
                                  if to_g > 0 else
                                  f"\n📈 +{diff} кг. Если цель набор — отлично 💪")
            else:
                weight_comment = "\n➡️ Вес стоит. Плато — нормально. Держим дисциплину 💪"

    weight_str = f"{weight_value} кг" if weight_value else "фото"
    target = get_user_target_weight(user_id)
    target_line = f"\n🎯 Цель: *{target} кг*" if target else ""
    await safe_send(context, user_id,
                    f"✅ *Вес записан: {weight_str}*\n"
                    f"⭐ *+{XP_WEIGH_IN} XP*  ·  🪙 *+{COINS_WEIGH_IN}*"
                    f"{trend}{weight_comment}{target_line}\n\n"
                    "_Данные сохранены. Так держим!_ 💪",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🎯 Изменить цель по весу",
                                             callback_data="weighin_set_target"),
                    ]]))
    weighin_states.pop(user_id, None)
    save_states()


# ── САМОЧУВСТВИЕ ──────────────────────────────────────────────────────────────
def get_schedule_days(user_id):
    try:
        row      = _get_client_row(user_id)
        days_str = row[5] if row and len(row) > 5 else ""
        if not days_str or "Гибк" in days_str:
            return []
        return [abbr for abbr in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"] if abbr in days_str]
    except Exception:
        return []


def wellbeing_done_today(user_id):
    try:
        rows      = _ws_rows("wellbeing", _TTL_SHEET)
        today_str = datetime.now(TIMEZONE).strftime("%d.%m.%Y")
        for r in rows[1:]:
            if len(r) > 1 and r[0] == str(user_id) and r[1] == today_str:
                return True
    except Exception:
        pass
    return False


async def _wellbeing_done_today(user_id) -> bool:
    """Сдан ли опрос сегодня. Сначала — мгновенная локальная отметка,
    и только потом обращение к таблице (в отдельном потоке)."""
    today_str = datetime.now(TIMEZONE).strftime("%d.%m.%Y")
    if wellbeing_done_marks.get(user_id) == today_str:
        return True
    try:
        done = await asyncio.to_thread(wellbeing_done_today, user_id)
    except Exception as e:
        logger.error(f"_wellbeing_done_today {user_id}: {e}")
        return False
    if done:
        wellbeing_done_marks[user_id] = today_str
    return done


def _mark_wellbeing_done(user_id):
    wellbeing_done_marks[user_id] = datetime.now(TIMEZONE).strftime("%d.%m.%Y")
    # Чистим устаревшие отметки, чтобы словарь не рос бесконечно
    today = wellbeing_done_marks[user_id]
    for uid in [u for u, d in wellbeing_done_marks.items() if d != today]:
        wellbeing_done_marks.pop(uid, None)


async def start_wellbeing_survey(chat_id, user_id, context, first_name="", username=""):
    wellbeing_states[user_id] = {
        "step": 0, "answers": [], "first_name": first_name, "username": username,
    }
    save_states()
    await safe_send(context, chat_id,
                    "💚 *Пара слов о самочувствии перед тренировкой*\n\n"
                    f"3 коротких вопроса — тренер будет знать о тебе больше, "
                    f"и ты получишь *+{XP_PER_WELLBEING} XP* 🎁")
    await ask_wellbeing_question(chat_id, user_id, context)


async def ask_wellbeing_question(chat_id, user_id, context):
    if user_id not in wellbeing_states:
        return
    step = wellbeing_states[user_id]["step"]
    if step >= len(WELLBEING_SURVEY):
        await finish_wellbeing_survey(chat_id, user_id, context)
        return
    q = WELLBEING_SURVEY[step]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"💚 *Вопрос {step + 1}/{len(WELLBEING_SURVEY)}:*\n\n{q['q']}",
        parse_mode="Markdown",
        reply_markup=wide_keyboard(q["options"], f"wbs_{step}"),
    )


async def wellbeing_survey_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    if user.id not in wellbeing_states:
        await query.edit_message_text("Опрос не активен.")
        return
    parts  = query.data.split("_")
    step   = int(parts[1]); opt = int(parts[2])
    answer = WELLBEING_SURVEY[step]["options"][opt]
    wellbeing_states[user.id]["answers"].append({"q": WELLBEING_SURVEY[step]["q"], "a": answer})
    wellbeing_states[user.id]["step"] = step + 1
    await query.edit_message_text(f"✅ {answer}")
    await ask_wellbeing_question(update.effective_chat.id, user.id, context)


def _get_wellbeing_range(user_id, date_from, date_to) -> list:
    """Записи самочувствия строго за период (для недельного отчёта)."""
    try:
        rows = _ws_rows_safe("wellbeing", _TTL_SHEET)
        out = []
        for r in rows[1:] if rows else []:
            if not r or r[0] != str(user_id) or len(r) < 2:
                continue
            d = _safe_date(r[1], "%d.%m.%Y")
            if d and date_from <= d <= date_to:
                out.append(r)
        return out
    except Exception:
        return []


def _get_wellbeing_trend(user_id: int, days: int = 4) -> list:
    """Возвращает последние N записей самочувствия (сон, энергия, мышцы)
    как список троек. Самая новая — последней."""
    try:
        rows = _ws_rows("wellbeing", _TTL_SHEET)
        user_rows = [r for r in rows[1:] if r and r[0] == str(user_id)]
        return user_rows[-days:] if user_rows else []
    except Exception:
        return []


def _wellbeing_score(row) -> int:
    """Оцениваем одну запись от 0 (всё плохо) до 6 (всё хорошо).
    Сон: Отлично=2, Нормально=1, Так себе=0, Не выспался=0
    Энергия: Полный=2, Норм=1, Низко=0, На нуле=0
    Мышцы: Восстановились=2, Лёгкая крепатура=1, Сильно болят=0"""
    score = 0
    if len(row) > 2:
        s = row[2].lower()
        if "отлично" in s or "💤" in s:           score += 2
        elif "нормально" in s or "🙂" in s:        score += 1
    if len(row) > 3:
        e = row[3].lower()
        if "полный" in e or "🔋" in e:             score += 2
        elif "норм" in e or "😌" in e:             score += 1
    if len(row) > 4:
        m = row[4].lower()
        if "восстановил" in m or "✅" in m:        score += 2
        elif "лёгкая" in m or "крепатура" in m:   score += 1
    return score


async def finish_wellbeing_survey(chat_id, user_id, context):
    state   = wellbeing_states.get(user_id, {})
    answers = state.get("answers", [])

    # ✅ Отмечаем сдачу СРАЗУ и локально: даже если запись в таблицу не пройдёт,
    # человек не застрянет в бесконечном опросе перед отчётом о тренировке.
    _mark_wellbeing_done(user_id)
    wellbeing_states.pop(user_id, None)
    save_states()

    saved_ok = True
    try:
        row = [str(user_id), datetime.now(TIMEZONE).strftime("%d.%m.%Y")] + \
              [a["a"] for a in answers]

        def _save_wellbeing():
            sheet = ws("wellbeing")
            _gs_call(sheet.append_row, row)
            _ws_invalidate("wellbeing")
        await asyncio.to_thread(_save_wellbeing)
    except Exception as e:
        saved_ok = False
        logger.error(f"wellbeing save error {user_id}: {e}")

    await add_xp(user_id, state.get("first_name", ""), state.get("username", ""),
                 XP_PER_WELLBEING, context=context)

    # ── Анализ тренда восстановления ─────────────────────────────────────────
    bad_days = 0
    try:
        trend_rows = await asyncio.to_thread(_get_wellbeing_trend, user_id, 4)
        if len(trend_rows) >= 2:
            # Считаем сколько подряд "плохих" дней (score < 3 из 6)
            for r in reversed(trend_rows):
                if _wellbeing_score(r) < 3:
                    bad_days += 1
                else:
                    break
    except Exception as e:
        logger.error(f"wellbeing trend {user_id}: {e}")

    if not saved_ok:
        logger.warning(f"wellbeing {user_id}: ответы не записаны, но поток не прерван")

    # Кнопки ведут на отчёт БЕЗ повторного опроса — суффикс _go
    workout_buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Отчёт за сегодня", callback_data="wk_today_go")],
        [InlineKeyboardButton("📅 Отчёт за вчера",   callback_data="wk_yesterday_go")],
    ])

    if bad_days >= 4:
        # Критический сигнал — 4+ дня подряд плохое восстановление
        recovery_msg = (
            "⚠️ *Стоп. Тело просит паузу.*\n\n"
            f"Уже {bad_days} дня подряд — усталость, недосып, тяжесть в мышцах.\n"
            "Это не слабость. Это сигнал.\n\n"
            "💡 *Больше ≠ лучше.* Прогресс происходит во время восстановления, "
            "а не во время тренировки. Без отдыха тело не растёт — оно ломается.\n\n"
            "Сегодня:\n"
            "• Если тренировка — снизь нагрузку на 30-40%\n"
            "• Приоритет: сон 8ч, еда, вода\n"
            "• Один полный день отдыха не убьёт прогресс — он его создаст\n\n"
            "📊 Давай посмотрим на питание — там часто скрыта причина:\n"
        )
        await safe_send(context, chat_id, recovery_msg,
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🥗 Отчёт по питанию", callback_data="food_done_init")],
                            [InlineKeyboardButton("🏋️ Всё равно тренировался", callback_data="wk_today_go")],
                            [InlineKeyboardButton("👇 Продолжить без отчёта", callback_data="food_skip_recovery")],
                        ]))
        # Уведомляем тренера
        display = get_display_name(user_id, state.get("first_name", ""))
        await _notify_trainer(
            context, user_id, display,
            f"⚠️ *Плохое восстановление {bad_days}+ дней подряд*\n\n"
            "Низкий сон, энергия и/или крепатура несколько дней.\n"
            "Возможно стоит снизить нагрузку или уточнить как дела."
        )
    elif bad_days >= 2:
        # Предупреждение — 2-3 дня плохого восстановления
        recovery_msg = (
            "💛 *Слушай, я смотрю на твоё самочувствие последние дни.*\n\n"
            "Сон и энергия не на высоте. Мышцы не успевают восстанавливаться.\n\n"
            "Сегодня на тренировке:\n"
            "• Следи за ощущениями — если что-то тянет или давит, сбавь\n"
            "• Лучше сделать меньше, но качественно\n"
            "• Хороший сон сегодня = хорошая тренировка завтра\n\n"
            "_Братство идёт далеко — и берёт себя в охапку, когда надо._ 💪"
        )
        await safe_send(context, chat_id, recovery_msg)
        await safe_send(context, chat_id,
                        f"💚 *Самочувствие записано!*  +{XP_PER_WELLBEING} XP\n\n"
                        "👇 *Теперь отчёт по тренировке:*",
                        reply_markup=workout_buttons)
    else:
        # Всё ок — сразу предлагаем отчёт по тренировке
        await safe_send(context, chat_id,
                        f"💚 *Принято!*  +{XP_PER_WELLBEING} XP\n\n"
                        "Тренер знает как ты себя чувствуешь — это важно для нагрузки.\n\n"
                        "👇 *Теперь жми!*\n"
                        "_Жду твой отчёт по тренировке._",
                        reply_markup=workout_buttons)


# ── ФОТО ─────────────────────────────────────────────────────────────────────
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id in weighin_states:
        caption = update.message.caption or ""
        kg_f    = parse_weight_float(caption) if caption else 0.0
        await save_weight(user.id, kg_f if kg_f > 0 else "", context,
                          photo_note="фото весов",
                          first_name=user.first_name or "", username=uname_of(user))
        return

    if user.id in food_states and food_states[user.id].get("mode") in ("collecting", "supplement"):
        state = food_states[user.id]
        if len(state["photos"]) >= 5:
            await update.message.reply_text("⚠️ Максимум 5 фото. Нажми «Готово».")
            return
        photo        = update.message.photo[-1]
        file         = await context.bot.get_file(photo.file_id)
        photo_bytes  = await file.download_as_bytearray()
        b64          = base64.b64encode(bytes(photo_bytes)).decode("utf-8")
        state["photos"].append(b64)
        if update.message.caption:
            state["meals"].append(update.message.caption)
        count = len(state["photos"])
        is_sup = "existing_text" in state
        btn_label = "✅ Готово — пересчитай КБЖУ!" if is_sup else "✅ Готово — анализируй!"
        await update.message.reply_text(
            f"📸 Фото {count}/5 принято! "
            f"{'Добавь ещё или жми «Готово».' if count < 5 else 'Максимум — жми «Готово».'}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(btn_label,      callback_data="food_done")],
                [InlineKeyboardButton("❌ Отмена", callback_data="food_cancel")],
            ]),
        )
        return

    if user_states.get(user.id, {}).get("mode") == "workout":
        await update.message.reply_text(
            "📸 Фото тренировок не нужны — напиши текстом:\n"
            "упражнения, веса, подходы.\n\n"
            "_Например: Присед 4х5х100кг, Жим 4х8х70кг_",
            parse_mode="Markdown",
        )
        return

    await safe_reply(update, "Для анализа питания сначала нажми 🥗 *Питание*.")


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in food_states and food_states[user.id].get("mode") in ("collecting", "supplement"):
        await update.message.reply_text(
            "⚠️ Голосовые пока не поддерживаются.\n\nНапиши *текстом* или пришли *фото еды*.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово", callback_data="food_done")],
                [InlineKeyboardButton("❌ Отмена", callback_data="food_cancel")],
            ]),
        )
        return
    await safe_reply(update, "Голосовые не обрабатываются. Для питания нажми 🥗 *Питание*.")


# ── РЕКОРДЫ ───────────────────────────────────────────────────────────────────
def get_last_records(user_id):
    try:
        rows      = _ws_rows("records", _TTL_SHEET)
        user_rows = [r for r in rows[1:] if len(r) > 1 and r[0] == str(user_id)]
        if not user_rows:
            return None
        last = user_rows[-1]
        return {"date": last[1] if len(last) > 1 else "—",
                "squat": last[2] if len(last) > 2 else "0",
                "bench": last[3] if len(last) > 3 else "0",
                "deadlift": last[4] if len(last) > 4 else "0",
                "pullup": last[5] if len(last) > 5 else "0",
                "sum": last[6] if len(last) > 6 else "0"}
    except Exception:
        return None


async def records_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user      = update.effective_user
    today     = datetime.now(TIMEZONE).date()
    last_date = await asyncio.to_thread(get_records_last_date, user.id)
    rec       = await asyncio.to_thread(get_last_records, user.id)

    # Всегда показываем текущие рекорды, если они есть
    if rec and rec.get("sum") not in (None, "", "0"):
        rec_text = (
            "🏆 *ТВОИ СИЛОВЫЕ РЕКОРДЫ*\n\n"
            f"🏋️ Приседания: *{rec['squat']} кг*\n"
            f"💪 Жим лёжа: *{rec['bench']} кг*\n"
            f"🔥 Становая: *{rec['deadlift']} кг*\n"
            f"🧗 Подтягивания (+вес): *{rec['pullup']} кг*\n"
            f"➖➖➖\n"
            f"📊 Сумма: *{rec['sum']} кг*\n"
            f"⭐ Начислено за рекорды: *+{rec['sum']} XP*\n"
            f"📅 Обновлено: {rec['date']}\n"
        )
    else:
        rec_text = (
            "🏆 *СИЛОВЫЕ РЕКОРДЫ*\n\n"
            "📭 Рекордов ещё нет — заполни и получи XP!\n"
        )

    # Проверяем, можно ли обновить
    if last_date:
        days_passed = (today - last_date).days
        if days_passed < RECORDS_SEASON_DAYS:
            days_left = RECORDS_SEASON_DAYS - days_passed
            rec_text += f"\n🔒 Обновить можно через *{days_left} {plural_days(days_left)}*"
            await safe_reply(update, rec_text, reply_markup=menu_for(user.id))
            return

    # Можно обновить — предлагаем заполнить
    rec_text += (
        "\n━━━━━━━━━━━━━\n"
        "📌 Каждый кг = *+1 XP!*\n"
        "Обновляется раз в сезон (3 месяца).\n\n"
        "Поехали обновлять 👇"
    )

    records_states[user.id] = {
        "step": 0, "answers": {},
        "first_name": user.first_name or "", "username": uname_of(user),
    }
    save_states()
    await safe_reply(update, rec_text, reply_markup=cancel_keyboard())
    await ask_records_question(update.effective_chat.id, user.id, context)


async def ask_records_question(chat_id, user_id, context):
    if user_id not in records_states:
        return
    step = records_states[user_id]["step"]
    if step >= len(RECORDS_EXERCISES):
        await finish_records(chat_id, user_id, context)
        return
    _, q_text = RECORDS_EXERCISES[step]
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🏆 *Упражнение {step + 1}/{len(RECORDS_EXERCISES)}:*\n\n{q_text}",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard(),
    )


async def finish_records(chat_id, user_id, context):
    state    = records_states.get(user_id, {})
    answers  = state.get("answers", {})
    total_kg = sum(answers.get(k, 0) for k in RECORDS_KEYS)
    try:
        await asyncio.to_thread(lambda: (
            ws("records").append_row([
                str(user_id), datetime.now(TIMEZONE).strftime("%d.%m.%Y"),
                answers.get("squat", 0), answers.get("bench", 0),
                answers.get("deadlift", 0), answers.get("pullup", 0), total_kg,
            ]),
            _ws_invalidate("records"),
        ))
    except Exception:
        pass
    mark_records_date(user_id)
    await add_xp(user_id, state.get("first_name", ""), state.get("username", ""),
                 total_kg, context=context)
    await safe_send(context, chat_id,
                    f"🏆 *Силовые зафиксированы!*\n"
                    f"━━━━━━━━━━━━━\n"
                    f"🏋️ Присед:     *{answers.get('squat', 0)} кг*\n"
                    f"💪 Жим лёжа:  *{answers.get('bench', 0)} кг*\n"
                    f"🔥 Становая:  *{answers.get('deadlift', 0)} кг*\n"
                    f"🧗 Подтяг.:   *+{answers.get('pullup', 0)} кг*\n"
                    f"━━━━━━━━━━━━━\n"
                    f"📊 Сумма: *{total_kg} кг*\n"
                    f"⭐ Начислено: *+{total_kg} XP*\n\n"
                    "_Обновить можно через 3 месяца (новый сезон)._ 💪",
                    reply_markup=menu_for(user_id))
    del records_states[user_id]
    save_states()


# ── MESSAGE HANDLER ────────────────────────────────────────────────────────────
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user       = update.effective_user
    text       = update.message.text or ""
    is_trainer = user.id == TRAINER_ID

    # Отмена любой активной сессии
    if text == BTN_CANCEL or "Отмена" in text:
        for d in (user_states, anketa_states, food_states, goals_test_states,
                  schedule_states, records_states, weighin_states, wellbeing_states):
            d.pop(user.id, None)
        save_states()
        await safe_reply(update, "Главное меню:", reply_markup=menu_for(user.id))
        return

    # Взвешивание
    if user.id in weighin_states:
        # Проверяем режим — обычный вес или ввод цели
        mode = weighin_states[user.id].get("mode", "waiting")
        if mode == "set_target":
            kg_f = parse_weight_float(text)
            if kg_f <= 0:
                await safe_reply(update, "⚠️ Не понял цифру. Напиши, например: `78.5`")
                return
            await _update_anketa_target_weight(user.id, kg_f)
            weighin_states.pop(user.id, None)
            save_states()
            await safe_reply(update,
                f"✅ *Цель обновлена: {kg_f} кг*\n\n"
                "_Теперь я буду отслеживать твой прогресс к этой цифре._ 🎯",
                reply_markup=menu_for(user.id))
            return
        # Обычное взвешивание — принимаем с десятичными
        kg_f = parse_weight_float(text)
        if kg_f <= 0:
            await safe_reply(update,
                "⚠️ Не понял цифру.\n"
                "Напиши вес числом, например `82.35` или `82,35`, или пришли фото весов.")
            return
        await save_weight(user.id, kg_f, context,
                          first_name=user.first_name or "", username=uname_of(user))
        return

    # Рекорды
    if user.id in records_states:
        step = records_states[user.id]["step"]
        key  = RECORDS_KEYS[step]
        records_states[user.id]["answers"][key] = parse_kg(text)
        records_states[user.id]["step"] = step + 1
        await ask_records_question(update.effective_chat.id, user.id, context)
        return

    # Анкета
    if user.id in anketa_states and anketa_states[user.id].get("waiting_text"):
        step = anketa_states[user.id]["step"]
        key  = ANKETA_KEYS[step]
        anketa_states[user.id]["answers"][key] = text
        anketa_states[user.id]["step"]         = step + 1
        anketa_states[user.id]["waiting_text"] = False
        await ask_anketa_question(update.effective_chat.id, user.id, context)
        return

    # Правки к тренировке
    if user_states.get(user.id, {}).get("mode") == "workout_append":
        note = text.strip()
        try:
            # Читаем таблицу и готовим данные для обновления — синхронно в потоке
            def _load_ctx_append():
                return (build_coach_context(user.id), get_coaching_style(user.id),
                        get_workout_history(user.id, limit=6))
            coach_ctx, style, history = await asyncio.to_thread(_load_ctx_append)

            def _find_and_update():
                sheet     = ws("workouts")
                rows      = sheet.get_all_values()
                today_str = datetime.now(TIMEZONE).strftime("%d.%m.%Y")
                target_i  = None
                existing_log = existing_text = existing_slog = ""
                for i in range(len(rows) - 1, 0, -1):
                    r = rows[i]
                    if len(r) > 1 and r[1] == str(user.id) and r[0].startswith(today_str):
                        target_i      = i + 1
                        existing_log  = r[4] if len(r) > 4 else ""
                        existing_text = r[5] if len(r) > 5 else ""
                        existing_slog = r[4] if len(r) > 4 else ""
                        break
                return target_i, existing_log, existing_text, existing_slog, sheet

            target_i, existing_log, existing_text, existing_slog, sheet = \
                await asyncio.to_thread(_find_and_update)

            if target_i:
                full_text = (f"{existing_text}\nДОПОЛНЕНИЕ:\n{note}"
                             if existing_text else note)
                result = await asyncio.to_thread(
                    analyze_workout, full_text, history, coach_ctx, style, ""
                )
                new_log    = result.get("workout_log", existing_log)
                new_slog   = result.get("strength_log", existing_slog)
                new_assess = result.get("assessment", "")[:200]

                def _write_append():
                    sheet.update_cell(target_i, 4, new_assess)
                    sheet.update_cell(target_i, 5, new_slog[:1000])
                    _ws_invalidate("workouts")

                await asyncio.to_thread(_write_append)
                await safe_reply(update,
                                 f"✅ *Обновлено!*\n\n_{new_assess}_\n\nБратство зафиксировало. 💪",
                                 reply_markup=menu_for(user.id))
            else:
                await safe_reply(update, "✅ Записал!", reply_markup=menu_for(user.id))
        except Exception as e:
            logger.error(f"workout_append error: {e}")
            await safe_reply(update, "✅ Записал!", reply_markup=menu_for(user.id))
        user_states.pop(user.id, None)
        save_states()
        return

    # Основной отчёт о тренировке
    if user_states.get(user.id, {}).get("mode") == "workout":
        # Защита от двойного нажатия
        if user.id in _processing:
            await safe_reply(update, "⏳ Уже обрабатываю твой отчёт...")
            return
        _processing.add(user.id)
        try:

            # Rate-limit
            ok, wait_sec = _ai_rate_ok(user.id)
            if not ok:
                _processing.discard(user.id)
                await safe_reply(update,
                                 f"⏳ Чуть подожди — {wait_sec} сек. до следующего анализа.")
                return
            _ai_mark(user.id)

            # Все чтения таблиц — в поток: пока идёт запрос, бот отвечает другим
            def _load_ctx():
                return (build_coach_context(user.id), get_coaching_style(user.id),
                        format_strength_chart(get_strength_dynamics(user.id)),
                        get_workout_history(user.id, limit=8))
            try:
                coach_ctx, style, chart, history = await asyncio.to_thread(_load_ctx)
            except Exception as e:
                logger.error(f"workout ctx load {user.id}: {e}")
                coach_ctx, style, chart, history = "", "", "", []

            for_date_str = user_states.get(user.id, {}).get("for_date") or \
                           datetime.now(TIMEZONE).strftime("%d.%m.%Y")
            try:
                for_date = datetime.strptime(for_date_str, "%d.%m.%Y").date()
            except Exception:
                for_date     = datetime.now(TIMEZONE).date()
                for_date_str = for_date.strftime("%d.%m.%Y")

            # Claude-вызов — в поток. Fallback при ошибке.
            try:
                result = await asyncio.to_thread(
                    analyze_workout, text, history, coach_ctx, style, chart
                )
            except Exception as e:
                logger.error(f"Claude workout fallback: {e}")
                result = {"completed": "да",
                          "assessment": "Тренировка записана. AI-анализ временно недоступен.",
                          "notes": "", "next_focus": "", "strength_log": ""}

            # Запись в таблицу — тоже в поток
            def _write_workout():
                ws("workouts").append_row([
                    for_date_str, str(user.id), uname_of(user),
                    result.get("assessment", "")[:300],       # col4: AI-отчёт
                    result.get("strength_log", "")[:1000],    # col5: основная часть + гипертрофия
                ])
                _ws_invalidate("workouts")

            try:
                await asyncio.to_thread(_write_workout)
            except Exception as e:
                logger.error(f"workout write error: {e}")

            # Уведомление тренеру — сводка БЕЗ текста клиента
            try:
                display = get_display_name(user.id, user.first_name)
                assess_full = (result.get("assessment", "") or "")[:300]
                slog_full   = (result.get("strength_log", "") or "")[:500]
                notes_full  = (result.get("notes", "") or "")[:200]
                next_f      = (result.get("next_focus", "") or "")[:200]

                trainer_msg = f"🏋️ *Тренировка сдана!*\n📅 {for_date_str}\n\n"
                if assess_full:
                    trainer_msg += f"📊 {assess_full}\n\n"
                if slog_full:
                    trainer_msg += f"📋 *Силовые:* {slog_full}\n\n"
                if notes_full:
                    trainer_msg += f"📝 {notes_full}\n"
                if next_f:
                    trainer_msg += f"🎯 {next_f}"

                await _notify_trainer(context, user.id, display, trainer_msg)
            except Exception:
                pass

            streak, xp, coins, old_lvl, new_lvl = await update_workout_progress(
                user.id, user.first_name or "", uname_of(user), context, workout_date=for_date
            )

            level_name, prev_thr, next_thr = get_level_name(xp)
            xp_to_next = (next_thr - xp) if next_thr else 0
            emoji      = {"да": "✅", "нет": "❌", "частично": "⚠️"}.get(
                result.get("completed", "да"), "✅")
            day_note   = f"📅 За {for_date_str}\n" if for_date != datetime.now(TIMEZONE).date() else ""
            next_focus = result.get("next_focus", "")
            notes      = result.get("notes", "")

            # Прогресс-бар
            bar_str = ""
            if next_thr and (next_thr - prev_thr) > 0:
                pct    = int((xp - prev_thr) / (next_thr - prev_thr) * 100)
                filled = int(14 * pct / 100)
                bar_str = f"\n`{'█' * filled}{'░' * (14 - filled)}` {pct}%  →  {xp_to_next} XP до ранга"

            # Мотивирующие фразы по стрику
            STREAK_PHRASES = {
                1: "Начало положено 🔥",
                3: "3 дня подряд — уже привычка начинается 💪",
                5: "5 тренировок! Братство чувствует твой темп 💪",
                7: "Неделя без пропусков — это серьёзно 🔥",
                10: "10 подряд. Ты — машина. 🤖",
                14: "Две недели! Тело уже не то что раньше 💥",
                21: "21 день — это уже не стрик, это характер 👑",
            }
            streak_phrase = next((v for k, v in sorted(STREAK_PHRASES.items(), reverse=True)
                                  if streak >= k), None)

            msg = (
                f"{emoji} *{result['assessment']}*\n"
                f"━━━━━━━━━━━━━\n"
                f"{day_note}"
                f"⭐ *+{XP_PER_WORKOUT} XP*  ·  🪙 *+{COINS_PER_WORKOUT}*\n"
                f"💎 Всего: *{xp} XP*  ·  🪙 {coins}\n"
                f"👑 {level_name}{bar_str}\n"
                f"🔥 Стрик: *{streak} {plural_days(streak)}*"
            )
            if streak_phrase:
                msg += f"  —  _{streak_phrase}_"
            if notes:
                msg += f"\n\n📝 *Заметка тренера:* {notes}"
            if next_focus:
                msg += f"\n\n🎯 *Следующая:* {next_focus}"
            msg += "\n━━━━━━━━━━━━━"

            user_states.pop(user.id, None)
            save_states()
            _processing.discard(user.id)
            await safe_reply(update, msg,
                             reply_markup=InlineKeyboardMarkup([
                                 [InlineKeyboardButton("🥗 Сдать питание",    callback_data="food_start_quick")],
                                 [InlineKeyboardButton("Спасибо, понял 🙏", callback_data="feedback_ack")],
                             ]))
        except Exception as e:
            _processing.discard(user.id)
            logger.error(f"workout processing error {user.id}: {e}")
            user_states.pop(user.id, None)
            save_states()
            await safe_reply(update, "⚠️ Произошла ошибка при обработке. Попробуй ещё раз.",
                             reply_markup=menu_for(user.id))
        await check_workout_streak_bonus(user.id, user.first_name or "", uname_of(user), context)
        return

    # Сбор питания
    if user.id in food_states and food_states[user.id].get("mode") in ("collecting", "supplement"):
        food_states[user.id]["meals"].append(text)
        is_sup    = "existing_text" in food_states[user.id]
        btn_label = "✅ Готово — пересчитай КБЖУ!" if is_sup else "✅ Готово — анализируй!"
        await update.message.reply_text(
            "✅ Записал! Добавь ещё или жми «Готово».",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(btn_label,      callback_data="food_done")],
                [InlineKeyboardButton("❌ Отмена", callback_data="food_cancel")],
            ]),
        )
        return

    # Роутинг кнопок меню
    if "Моя статистика" in text or text == BTN_STATS:
        return await stats_cmd(update, context)
    if "Топ" in text or "ТОП" in text or text == BTN_TOP:
        return await leaderboard_cmd(update, context)
    if "Подогрев" in text or text == BTN_BONUS:
        return await bonus_cmd(update, context)
    if "Измерить" in text or "взвес" in text.lower():
        return await weighin_cmd(update, context)
    if "Расписание" in text:
        return await schedule_cmd(update, context)
    if "Тренировк" in text:
        return await workout_cmd(update, context)
    if "Питание" in text:
        return await food_cmd(update, context)
    if "Рекорды" in text or "1 кг = 1 XP" in text:
        return await records_cmd(update, context)
    if "Цели" in text:
        return await goals_cmd(update, context)
    if "Анкета" in text:
        return await anketa_cmd(update, context)
    if is_trainer and "Клиенты" in text:
        return await clients_cmd(update, context)
    if is_trainer and "Итоги" in text:
        return await week_cmd(update, context)

    await safe_reply(update, "Выбери действие в меню 👇", reply_markup=menu_for(user.id))


# ── ТРЕНЕР: КЛИЕНТЫ ───────────────────────────────────────────────────────────
def planned_workouts_per_week(client_row):
    try:
        n = parse_kg(client_row[4] if len(client_row) > 4 else "")
        return n if n > 0 else 0
    except Exception:
        return 0


WARMUP_Q_LABELS = {
    "train_0": "Что останавливает от тренировок", "train_1": "Сейчас с тренировками",
    "train_2": "Ради чего тренируется", "food_0": "Сейчас с едой",
    "food_1": "Где срывается в питании", "food_2": "Опыт с диетами",
    "name": "Имя", "age": "Возраст", "weight": "Вес", "height": "Рост",
    "target": "Цель по весу", "goal": "Главная цель",
    "health": "Здоровье / ограничения", "experience": "Опыт тренировок",
    "motivation": "Мотивация",
}


def get_warmup_answers(uid):
    out = []; seen = set()
    for sname in ["warmup_answers", "consultation_requests"]:
        try:
            rows = ws(sname).get_all_values()
            if not rows:
                continue
            data_rows = rows if (rows[0] and rows[0][0] and rows[0][0].isdigit()) else rows[1:]
            for r in data_rows:
                if not r or r[0] != str(uid):
                    continue
                if sname == "warmup_answers":
                    key = r[2] if len(r) > 2 else ""
                    val = r[3] if len(r) > 3 else ""
                    if val and key and key not in seen:
                        out.append((key, val)); seen.add(key)
                else:
                    if len(r) > 5 and r[5]:
                        try:
                            for key, val in json.loads(r[5]).items():
                                if val and key and key not in seen:
                                    out.append((key, str(val))); seen.add(key)
                        except Exception:
                            pass
                    break
        except Exception as e:
            logger.error(f"get_warmup_answers {sname}: {e}")
    return out


def build_dossier(uid):
    cl    = next((c for c in get_all_clients() if c and c[0] == uid), None)
    name  = display_name_for(cl) if cl else "—"
    uname = cl[2] if cl and len(cl) > 2 else "—"
    reg   = cl[3] if cl and len(cl) > 3 else "—"
    lines = [f"🗂 *ДОСЬЕ — {md_safe(name)}* ({md_safe(uname)})",
             f"🆔 `{uid}`  •  с {reg}", ""]

    # ── XP и Ранг ──
    try:
        p, _ = get_progress(int(uid)) if uid.isdigit() else ({}, False)
        if p:
            lvl, _, _ = get_level_name(p.get("xp", 0))
            lines += [
                f"⭐ *XP:* {p.get('xp', 0)} · *Монеты:* {p.get('coins', 0)} 🪙",
                f"🏅 *Ранг:* {lvl}",
                f"🏋️ Тренировок: {p.get('workouts', 0)} · 🥗 Дней питания: {p.get('food_days', 0)}",
                f"🔥 Стрик: {p.get('streak', 0)} дн.", ""
            ]
    except Exception:
        pass

    # ── Анкета (полные ответы) ──
    try:
        rows = _ws_rows("anketa", _TTL_SHEET)
        row  = next((r for r in rows if r and r[0] == uid), None)
        ALBL = {"name": "Имя", "age": "Возраст", "gender": "Пол", "height": "Рост",
                "weight": "Вес", "target_weight": "Желаемый вес", "health": "Здоровье",
                "nutrition": "Питание", "sleep": "Сон", "stress": "Стресс",
                "alcohol": "Алкоголь", "activity": "Активность", "experience": "Опыт",
                "motivation": "Мотивация", "psych": "Психотип", "extra": "Ещё"}
        if row:
            lines.append("📋 *АНКЕТА*")
            for j, key in enumerate(ANKETA_KEYS):
                val = row[2 + j] if len(row) > 2 + j else ""
                if val and val.strip():
                    lines.append(f"• {ALBL.get(key, key)}: {md_safe(val)}")
            lines.append("")
        else:
            lines += ["📋 *АНКЕТА*", "_не заполнена_", ""]
    except Exception:
        pass

    # ── КБЖУ-норма ──
    if uid.isdigit():
        nt = compute_nutrition_targets(int(uid))
        if nt:
            lines += ["🍽 *НОРМА КБЖУ (расчёт):*",
                      f"• Калории: {nt['kcal']} ккал",
                      f"• Белок: {nt['protein']} г | Жиры: {nt['fat']} г | Углеводы: {nt['carbs']} г",
                      f"• Режим: {nt['mode']}", ""]

    # ── Цели (полные ответы) ──
    try:
        goals_rows = _ws_rows("goals", _TTL_SHEET)
        goals_row = next((r for r in goals_rows[1:] if r and r[0] == uid), None)
        GLBL = ["Главное качество", "Второе качество", "Формат тренировок",
                "Результат за 3 мес", "Что мешает", "Дедлайн",
                "Глубинная цель", "Стиль работы"]
        if goals_row:
            lines.append("🎯 *ЦЕЛИ (полные ответы)*")
            vals = goals_row[2:10]
            for i, lbl in enumerate(GLBL):
                val = vals[i] if i < len(vals) else ""
                if val and val.strip():
                    lines.append(f"• {lbl}: {md_safe(val)}")
            lines.append("")
        else:
            lines += ["🎯 *ЦЕЛИ*", "_не заполнены_", ""]
    except Exception:
        goals = get_user_goals_summary(int(uid)) if uid.isdigit() else "не заполнены"
        if goals and goals != "не заполнены":
            lines.append("🎯 *ЦЕЛИ*")
            for part in goals.split("; "):
                if part.strip():
                    lines.append(f"• {md_safe(part)}")
            lines.append("")

    # ── Расписание ──
    if cl and len(cl) > 6 and any([cl[4], cl[5], cl[6]]):
        lines.append("📅 *РАСПИСАНИЕ*")
        if cl[4]: lines.append(f"• Частота: {md_safe(cl[4])}")
        if cl[5]: lines.append(f"• Дни: {md_safe(cl[5])}")
        if cl[6]: lines.append(f"• Время/длит.: {md_safe(cl[6])}")
        lines.append("")

    # ── Рекорды + XP от суммы ──
    rec = get_last_records(int(uid)) if uid.isdigit() else None
    if rec:
        lines += ["🏆 *СИЛОВЫЕ РЕКОРДЫ*",
                  f"• Присед: {rec['squat']} кг | Жим: {rec['bench']} кг",
                  f"• Становая: {rec['deadlift']} кг | Подтяг: +{rec['pullup']} кг",
                  f"• 📊 Сумма: *{rec['sum']} кг* → *+{rec['sum']} XP*",
                  f"• Обновлено: {rec['date']}", ""]
    else:
        lines += ["🏆 *СИЛОВЫЕ РЕКОРДЫ*", "_не заполнены_", ""]

    # ── Прогрев ──
    wa = get_warmup_answers(uid)
    if wa:
        lines.append("🔥 *ИЗ ПРОГРЕВА*")
        for key, val in wa:
            if val and str(val).strip():
                lines.append(f"• {WARMUP_Q_LABELS.get(key, key)}: {md_safe(val)}")
        lines.append("")

    # ── Самочувствие ──
    try:
        wb_rows = _ws_rows("wellbeing", _TTL_SHEET)
        user_wb = [r for r in wb_rows if r and r[0] == uid][-3:]
        if user_wb:
            lines.append("💚 *САМОЧУВСТВИЕ (посл. 3)*")
            for r in user_wb:
                lines.append(f"• {r[1] if len(r) > 1 else '—'}: "
                              f"{md_safe(', '.join(r[2:5])) if len(r) > 2 else '—'}")
            lines.append("")
    except Exception:
        pass

    # ── История веса ──
    try:
        wh = get_user_weight_history(int(uid), weeks=8) if uid.isdigit() else []
        if wh:
            lines.append("⚖️ *ИСТОРИЯ ВЕСА (посл. 8 замеров)*")
            for date_str, kg in wh:
                lines.append(f"• {date_str}: {kg} кг")
            if len(wh) >= 2:
                diff = wh[-1][1] - wh[0][1]
                sign = "+" if diff >= 0 else ""
                lines.append(f"📉 Изменение: *{sign}{diff:.1f} кг*")
            lines.append("")
    except Exception:
        pass

    return "\n".join(lines).strip()


async def admin_dashboard(update, context: ContextTypes.DEFAULT_TYPE):
    """/dashboard или кнопка — расширенная панель тренера с аналитикой."""
    # Работает и с Update (команда) и с CallbackQuery (кнопка из итогов недели)
    if hasattr(update, "callback_query") and update.callback_query:
        user_id = update.callback_query.from_user.id
        reply   = update.callback_query.message.reply_text
    elif hasattr(update, "from_user"):
        # передан сам query object
        user_id = update.from_user.id
        reply   = update.message.reply_text
    else:
        user_id = update.effective_user.id
        reply   = update.message.reply_text

    if user_id != TRAINER_ID:
        return

    clients = get_all_clients()
    if not clients:
        await reply("👥 Клиентов пока нет.")
        return

    today   = datetime.now(TIMEZONE).date()
    total   = len(clients)

    # Считаем активность и риски по кэшу прогресса
    prog_rows = _ws_rows_safe("progress", _TTL_ADMIN_READ)
    prog_map  = {}
    for r in (prog_rows[1:] if prog_rows else []):
        if r and r[0] and r[0].isdigit():
            prog_map[r[0]] = r

    def _ds(s):
        if not s: return 999
        try: return (today - datetime.strptime(s, "%Y-%m-%d").date()).days
        except: return 999

    active_week = active_month = 0
    critical_users = []
    warning_users  = []

    for c in clients:
        if not c or not c[0] or not c[0].isdigit():
            continue
        uid  = c[0]
        name = display_name_for(c)
        pr   = prog_map.get(uid, [])
        dw   = _ds(pr[8] if len(pr) > 8 else "")
        df   = _ds(pr[9] if len(pr) > 9 else "")
        best = min(dw, df)

        if best <= 7:  active_week  += 1
        if best <= 30: active_month += 1

        risk = compute_churn_risk(int(uid))
        if risk["level"] == "critical":
            critical_users.append((name, uid, risk))
        elif risk["level"] == "warning":
            warning_users.append((name, uid, risk))

    pct_week  = int(active_week  / total * 100) if total else 0
    pct_month = int(active_month / total * 100) if total else 0
    n_risk    = len(critical_users) + len(warning_users)

    text = (
        f"📊 *ДАШБОРД БРАТСТВА*\n"
        f"━━━━━━━━━━━━━\n\n"
        f"👥 Всего клиентов: *{total}*\n"
        f"🔥 Активны на неделе: *{active_week}* ({pct_week}%)\n"
        f"📅 Активны в месяце: *{active_month}* ({pct_month}%)\n\n"
        f"⚠️ Требуют внимания: *{n_risk}*\n"
        f"🔴 Критично: *{len(critical_users)}*\n"
        f"🟡 Предупреждение: *{len(warning_users)}*\n"
    )

    if critical_users:
        text += "\n━━━━━━━━━━━━━\n🔴 *Критичные — написать сейчас:*\n"
        for name, uid, risk in critical_users[:5]:
            reasons_str = ", ".join(risk["reasons"][:2])
            text += f"• *{md_safe(name)}* — {reasons_str}\n"

    if warning_users:
        text += "\n🟡 *Предупреждение:*\n"
        for name, uid, risk in warning_users[:5]:
            reasons_str = ", ".join(risk["reasons"][:1])
            text += f"• *{md_safe(name)}* — {reasons_str}\n"

    await reply(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Полный список",       callback_data="admin_clients")],
            [InlineKeyboardButton("🔴 Связаться с риском", callback_data="admin_risk_contact")],
        ])
    )


async def admin_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != TRAINER_ID:
        return
    data = query.data
    if data == "admin_clients":
        await query.message.reply_text("👇 Список клиентов:", reply_markup=main_keyboard(True, False))
        await clients_cmd_from_msg(query.message, context)
    elif data == "admin_week":
        await query.message.reply_text("📈 Открываю итоги...")
    elif data == "admin_risk_contact":
        # Показываем критичных с кнопками для быстрого контакта
        clients = get_all_clients()
        today   = datetime.now(TIMEZONE).date()
        for c in clients:
            if not c or not c[0] or not c[0].isdigit():
                continue
            risk = compute_churn_risk(int(c[0]))
            if risk["level"] == "critical":
                name = display_name_for(c)
                uname = c[2] if len(c) > 2 else ""
                reasons_str = "\n".join(f"• {r}" for r in risk["reasons"])
                await query.message.reply_text(
                    f"🔴 *{md_safe(name)}*\n"
                    f"`{c[0]}`{f' ({md_safe(uname)})' if uname else ''}\n\n"
                    f"Риск: *{risk['risk']}%*\n{reasons_str}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("📊 Досье", callback_data=f"cl_dossier_{c[0]}"),
                        InlineKeyboardButton("📊 Стат.", callback_data=f"cl_stat_{c[0]}"),
                    ]])
                )


async def clients_cmd_from_msg(message, context):
    """Вспомогательная — клиенты из любого сообщения."""
    try:
        ranked = await asyncio.to_thread(lambda: _build_ranked(*_read_all_sheets()))
        medals = ["🥇", "🥈", "🥉"]
        for i, u in enumerate(ranked):
            uid  = u["id"]
            name = u["name"]
            rank = medals[i] if i < 3 else f"{i+1}."
            kb   = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Стат.", callback_data=f"cl_stat_{uid}"),
                 InlineKeyboardButton("🗂 Досье",  callback_data=f"cl_dossier_{uid}")],
            ])
            await message.reply_text(
                f"{rank} *{md_safe(name)}*  ·  {u['xp']} XP  ·  {u['level']}",
                parse_mode="Markdown", reply_markup=kb)
    except Exception as e:
        logger.error(f"clients_cmd_from_msg: {e}")


async def clients_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TRAINER_ID:
        return
    try:
        ranked = await asyncio.to_thread(lambda: _build_ranked(*_read_all_sheets()))
    except Exception as e:
        logger.error(f"clients_cmd error: {e}")
        await safe_reply(update, "⚠️ Не удалось загрузить клиентов.")
        return
    if not ranked:
        await safe_reply(update, "👥 Клиентов пока нет.")
        return
    medals = ["🥇", "🥈", "🥉"]
    await safe_reply(update,
                     f"👥 *КЛИЕНТЫ* — всего {len(ranked)}\n_По XP_",
                     reply_markup=InlineKeyboardMarkup([[
                         InlineKeyboardButton("🗑 Удалить ВСЕХ", callback_data="delall_ask"),
                     ]]))
    for i, u in enumerate(ranked):
        uid   = u["id"]
        name  = u["name"]
        medal = medals[i] if i < 3 else f"{i + 1}."
        await update.message.reply_text(
            f"{medal} *{md_safe(name)}*\n"
            f"💪 {u['workouts']} трен.  •  ⭐ {u['xp']} XP  •  {u['level']}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Статистика", callback_data=f"cl_stat_{uid}"),
                 InlineKeyboardButton("🗂 Досье",       callback_data=f"cl_dossier_{uid}")],
                [InlineKeyboardButton(f"🗑 Удалить {name}", callback_data=f"cl_del_{uid}")],
            ]),
        )


async def client_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != TRAINER_ID:
        return
    parts = query.data.split("_", 2)
    kind  = parts[1]
    uid   = parts[2]

    if kind == "stat":
        try:
            prog_rows = _ws_rows_safe("progress", _TTL_ADMIN_READ)
            pd = {"xp": 0, "streak": 0, "max_streak": 0, "coins": 0,
                  "food_streak": 0, "food_max_streak": 0}
            for r in prog_rows:
                if r and r[0] == uid:
                    pd = {
                        "streak":          int(r[3])  if len(r) > 3  and r[3]  else 0,
                        "max_streak":      int(r[4])  if len(r) > 4  and r[4]  else 0,
                        "xp":              int(r[5])  if len(r) > 5  and r[5]  else 0,
                        "coins":           int(r[7])  if len(r) > 7  and r[7]  else 0,
                        "food_streak":     int(r[10]) if len(r) > 10 and r[10] else 0,
                        "food_max_streak": int(r[11]) if len(r) > 11 and r[11] else 0,
                    }; break
            lvn, pvt, nxt = get_level_name(pd["xp"])
            cl_rows = _ws_rows("clients",  _TTL_ADMIN_READ)
            cl      = next((c for c in cl_rows if c and c[0] == uid), None)
            name    = display_name_for(cl) if cl else "—"
            uname   = cl[2] if cl and len(cl) > 2 else "—"
            total   = sum(1 for r in _ws_rows("workouts", _TTL_ADMIN_READ) if len(r) > 1 and r[1] == uid)
            fd      = count_food_days(int(uid))
            wh      = get_user_weight_history(int(uid))
            nt_str  = ""
            if uid.isdigit():
                nt = compute_nutrition_targets(int(uid))
                if nt:
                    nt_str = (f"\n🍽 Норма: {nt['kcal']} ккал · белок {nt['protein']}г "
                              f"({nt['mode']})")
            bar = ""
            if nxt and (nxt - pvt) > 0:
                pct    = int((pd["xp"] - pvt) / (nxt - pvt) * 100)
                filled = int(20 * pct / 100)
                bar    = f"\n{'█' * filled}{'░' * (20 - filled)} {pct}%"
            wl = "нет данных"
            if wh:
                wl = " → ".join(f"{kg}кг" for _, kg in wh[-3:])
                if len(wh) >= 2:
                    diff = wh[-1][1] - wh[-2][1]
                    wl  += f" ({'📈 +' if diff > 0 else '📉 '}{diff} кг)"
            text = (f"📊 *СТАТИСТИКА — {md_safe(name)}* ({md_safe(uname)})\n\n"
                    f"👑 {lvn} · *{pd['xp']} XP*{bar}\n\n"
                    f"💪 Тренировок: *{total}*\n"
                    f"🔥 Стрик трен: *{pd['streak']}* (макс: {pd['max_streak']})\n"
                    f"🥗 Питание: *{fd}* {plural_days(fd)} · стрик *{pd['food_streak']}* (макс: {pd['food_max_streak']})\n"
                    f"🪙 Монет: *{pd['coins']}*\n"
                    f"⚖️ Вес: {wl}{nt_str}")
            await safe_send(context, query.message.chat_id, text)
        except Exception as e:
            logger.error(f"client stat error {uid}: {e}")
            await query.message.reply_text("⚠️ Не удалось загрузить статистику.")

    elif kind == "dossier":
        try:
            text = build_dossier(uid)
            for chunk in range(0, len(text), 3800):
                await safe_send(context, query.message.chat_id, text[chunk:chunk + 3800])
        except Exception as e:
            logger.error(f"client dossier error {uid}: {e}")
            await query.message.reply_text("⚠️ Не удалось собрать досье.")

    elif kind == "del":
        cl   = next((c for c in get_all_clients() if c and c[0] == uid), None)
        name = display_name_for(cl) if cl else uid
        await query.message.reply_text(
            f"⚠️ Отключить *{md_safe(name)}* и удалить все данные?\n_Необратимо._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🗑 Да, отключить {name}", callback_data=f"cl_delyes_{uid}")],
                [InlineKeyboardButton("❌ Отмена",                 callback_data="cl_delno_0")],
            ]),
        )
    elif kind == "delno":
        await query.edit_message_text("❌ Удаление отменено.")
    elif kind == "delyes":
        cl   = next((c for c in get_all_clients() if c and c[0] == uid), None)
        name = display_name_for(cl) if cl else uid
        await query.edit_message_text(f"⏳ Отключаю {name}...")
        try:
            await context.bot.send_message(
                int(uid),
                "🙏 Спасибо, что был с нами.\n\nТвой доступ приостановлен.\n"
                "Если захочешь продолжить — напиши лично 👇\nhttps://t.me/coach_tonny",
            )
        except Exception:
            pass
        try:
            await asyncio.to_thread(_dump_backup, f"before_del_{uid}")
        except Exception as e:
            logger.error(f"backup before client delete {uid}: {e}")
        _journal("DELETE_CLIENT", uid, {"by": "trainer", "hw": hw_get(uid)})
        delete_user_everywhere(uid)
        await query.message.reply_text(f"✅ *{md_safe(name)}* отключён.",
                                       parse_mode="Markdown",
                                       reply_markup=main_keyboard(True, False))


def _delete_rows_for_user(sheet_name, uid, id_col=0):
    try:
        sheet = ws(sheet_name)
        rows  = sheet.get_all_values()
        to_del = [i + 1 for i, r in enumerate(rows)
                  if len(r) > id_col and r[id_col] == str(uid)]
        for row_idx in reversed(to_del):
            try:
                sheet.delete_rows(row_idx)
            except Exception as e:
                logger.error(f"delete row {row_idx} from {sheet_name}: {e}")
        if to_del:
            _ws_invalidate(sheet_name)
    except Exception as e:
        logger.error(f"delete rows {sheet_name} for {uid}: {e}")


def delete_user_everywhere(uid):
    for sname, col in [
        ("clients", 0), ("progress", 0), ("workouts", 1), ("food_log", 0),
        ("weight_log", 0), ("goals", 0), ("anketa", 0), ("plan_bonus", 0),
        ("records", 0), ("wellbeing", 0), ("warmup_transfer", 0),
        ("warmup_imported", 0), ("warmup_answers", 0),
    ]:
        _delete_rows_for_user(sname, uid, id_col=col)
    _c_del_user(uid)
    # Убираем из несгораемых марок, иначе автоаудит восстановит удалённого
    with _HW_LOCK:
        _HW.pop(str(uid), None)
    hw_save(True)
    if str(uid).isdigit():
        uid_i = int(uid)
        for d in (user_states, anketa_states, food_states, goals_test_states,
                  schedule_states, records_states, weighin_states, wellbeing_states):
            d.pop(uid_i, None)


async def delete_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != TRAINER_ID:
        return
    if query.data == "delall_ask":
        await query.message.reply_text(
            "⚠️ *ПОЛНОЕ УДАЛЕНИЕ*\n\nВсе клиенты и данные будут удалены.\n\nПродолжить?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 ДА, удалить ВСЁ", callback_data="delall_yes")],
                [InlineKeyboardButton("❌ Отмена",            callback_data="delall_no")],
            ]),
        )
    elif query.data == "delall_no":
        await query.edit_message_text("❌ Удаление отменено.")
    elif query.data == "delall_yes":
        await query.edit_message_text("⏳ Резервная копия перед удалением...")
        try:
            backup_path = await asyncio.to_thread(_dump_backup, "before_delete_all")
        except Exception as e:
            logger.error(f"backup before delall: {e}")
            await query.message.reply_text(
                "⚠️ Резервную копию сделать не удалось — удаление отменено.")
            return
        await query.message.reply_text("⏳ Очищаю все таблицы...")
        for sn in ["clients", "progress", "workouts", "food_log", "weight_log",
                   "goals", "anketa", "plan_bonus", "records", "wellbeing"]:
            clear_sheet_keep_headers(sn)
        with _HW_LOCK:
            _HW.clear()
        hw_save(True)
        for d in (user_states, anketa_states, food_states, goals_test_states,
                  schedule_states, records_states, weighin_states, wellbeing_states):
            d.clear()
        _CACHE.clear()
        save_states()
        await query.message.reply_text(
            f"✅ *Готово!*\n💾 Копия: `{backup_path}`", parse_mode="Markdown",
            reply_markup=main_keyboard(True, False))


def plan_bonus_given(user_id, week_start):
    try:
        rows = _gs_call(ws("plan_bonus").get_all_values)[1:]
        wk   = week_start.strftime("%d.%m.%Y")
        return any(len(r) > 1 and r[0] == str(user_id) and r[1] == wk for r in rows)
    except Exception:
        return False


def mark_plan_bonus(user_id, week_start):
    try:
        _gs_call(ws("plan_bonus").append_row,
                 [str(user_id), week_start.strftime("%d.%m.%Y")])
    except Exception as e:
        logger.error(f"mark_plan_bonus {user_id}: {e}")


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TRAINER_ID:
        return
    await safe_reply(update,
                     "⚠️ *СБРОС ДАННЫХ*\n\n"
                     "Очистятся: прогресс, XP, тренировки, питание, вес, цели, анкеты.\n"
                     "*Список клиентов сохранится.*\n\nПродолжить?",
                     reply_markup=InlineKeyboardMarkup([
                         [InlineKeyboardButton("🗑️ Да, сбросить", callback_data="reset_yes")],
                         [InlineKeyboardButton("❌ Отмена",         callback_data="reset_no")],
                     ]))


async def reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != TRAINER_ID:
        return
    if query.data == "reset_no":
        await query.edit_message_text("❌ Сброс отменён.")
        return
    await query.edit_message_text("⏳ Делаю резервную копию перед сбросом...")
    backup_path = None
    try:
        backup_path = await asyncio.to_thread(_dump_backup, "before_reset")
    except Exception as e:
        logger.error(f"backup before reset: {e}")
        await safe_send(context, query.from_user.id,
                        "⚠️ Не удалось сделать резервную копию — сброс отменён.\n"
                        "Проверь доступ к таблице и попробуй позже.")
        return
    await safe_send(context, query.from_user.id, "⏳ Сбрасываю...")
    for sn in ["progress", "workouts", "food_log", "weight_log",
               "goals", "anketa", "plan_bonus", "records", "wellbeing"]:
        clear_sheet_keep_headers(sn)
    with _HW_LOCK:
        _HW.clear()
    hw_save(True)
    try:
        sheet = ws("clients")
        rows  = sheet.get_all_values()
        for i, r in enumerate(rows[1:], start=2):
            if not r or not r[0]:
                continue
            sheet.update(f"A{i}:M{i}", [[
                r[0], r[1] if len(r) > 1 else "", r[2] if len(r) > 2 else "",
                r[3] if len(r) > 3 else "",
                "", "", "", "нет", "0", "0", "нет", "нет", "",
            ]])
        _ws_invalidate("clients")
    except Exception as e:
        logger.error(f"reset clients: {e}")
    for d in (user_states, anketa_states, food_states, goals_test_states,
              schedule_states, records_states, weighin_states, wellbeing_states):
        d.clear()
    _CACHE.clear()
    save_states()
    await safe_send(context, query.from_user.id,
                    "✅ *Готово!* Данные сброшены.\n"
                    f"💾 Резервная копия: `{backup_path}`",
                    reply_markup=main_keyboard(True, False))


async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TRAINER_ID:
        return
    try:
        clients = get_all_clients()
    except Exception as e:
        logger.error(f"week_cmd error: {e}")
        await safe_reply(update, "⚠️ Не удалось загрузить.")
        return
    valid = [c for c in clients if c and c[0]]
    if not valid:
        await safe_reply(update, "Данных пока нет.")
        return
    today = datetime.now(TIMEZONE).date()
    # В понедельник считаем прошлую неделю целиком, в остальные дни — текущую
    week_start, week_end = report_week_bounds(today)
    w_rows     = _ws_rows_safe("workouts", _TTL_ADMIN_READ)
    f_rows     = _ws_rows_safe("food_log",  _TTL_ADMIN_READ)
    closed     = today.weekday() == 0
    text = (f"📈 *ИТОГИ НЕДЕЛИ — ПЛАН vs ФАКТ*\n"
            f"_{fmt_period(week_start, week_end)}"
            f"{' · завершённая неделя' if closed else ' · неделя идёт'}_\n\n")
    completed_count = 0
    bonus_awarded   = []
    for c in valid:
        uid     = c[0]
        name    = display_name_for(c)
        planned = planned_workouts_per_week(c)
        fact_w  = sum(1 for r in w_rows[1:]
                      if len(r) > 1 and r[1] == uid and
                      _date_in_week(r[0].split()[0], week_start, week_end))
        fact_f  = len({r[2] for r in f_rows[1:]
                       if len(r) > 2 and r[0] == uid and
                       _date_in_week(r[2], week_start, week_end)})
        plan_done = planned > 0 and fact_w >= planned
        if plan_done:
            completed_count += 1
        bonus_note = ""
        if plan_done and not plan_bonus_given(uid, week_start):
            try:
                await add_xp(int(uid), c[1] or name, c[2] or "",
                             XP_PLAN_DONE, coins_amount=COINS_PLAN_DONE, context=context)
                mark_plan_bonus(uid, week_start)
                bonus_note = f"  🎁 +{XP_PLAN_DONE} XP +{COINS_PLAN_DONE}🪙"
                bonus_awarded.append(name)
                try:
                    await safe_send(context, int(uid),
                                    f"🎁 *Бонус за выполнение плана!*\n\n"
                                    f"Тренировок: *{fact_w}/{planned}*!\n"
                                    f"Награда: *+{XP_PLAN_DONE} XP* и 🪙 *+{COINS_PLAN_DONE}*\n\n"
                                    "Дисциплина — твоя суперсила. 🔥")
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"plan bonus error {uid}: {e}")
        status = "✅" if plan_done else ("🟡" if fact_w > 0 else "🔴")
        plan_s = f"{fact_w}/{planned}" if planned else f"{fact_w}/—"
        text  += f"{status} *{md_safe(name)}*\n   💪 {plan_s} · 🥗 {fact_f}{bonus_note}\n\n"
    text += f"━━━━━━━━━━━━━━━\n✅ Выполнили план: {completed_count}/{len(valid)}\n"
    text += (f"🎁 Бонус: {', '.join(md_safe(n) for n in bonus_awarded)}"
             if bonus_awarded else "_Новых бонусов не начислено._")
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=main_keyboard(True, False))
    await update.message.reply_text(
        "👇 Подробная аналитика по клиентам:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 Открыть дашборд", callback_data="admin_dashboard_open"),
        ]])
    )


# ── ДЖОБЫ ────────────────────────────────────────────────────────────────────
def _date_in_week(date_str, week_start, week_end):
    try:
        d = datetime.strptime(date_str.split()[0], "%d.%m.%Y").date()
        return week_start <= d <= week_end
    except Exception:
        return False


async def send_weekly_nutrition_reports(context: ContextTypes.DEFAULT_TYPE):
    """Пн 10:00 — недельный AI-отчёт каждому клиенту.

    Приходит в ПОНЕДЕЛЬНИК утром, но подводит итоги ПОЛНОЙ ПРОШЛОЙ недели
    (пн 00:00 — вс 23:59). Так в отчёт попадают и воскресные тренировки,
    и вечерние отчёты за выходные."""
    today = datetime.now(TIMEZONE).date()
    if today.weekday() != 0:  # 0 = понедельник
        return
    week_start, week_end = prev_week_bounds(today)
    logger.info(f"Weekly reports for period {week_start} — {week_end}")

    clients = [c for c in get_all_clients() if c and c[0]]
    if not clients:
        return
    # Прогреваем кэш один раз. Если ключевые листы недоступны — отчёт
    # НЕ отправляем: лучше пропустить, чем прислать пустые итоги.
    try:
        _ws_rows("workouts", _TTL_ADMIN_READ)
        _ws_rows("food_log", _TTL_ADMIN_READ)
    except Exception as e:
        logger.error(f"weekly reports aborted — нет данных: {e}")
        try:
            await safe_send(context, TRAINER_ID,
                            "⚠️ Недельные отчёты не отправлены: Google Sheets "
                            "недоступен. Повтори командой позже.")
        except Exception:
            pass
        return
    for sn in ("goals", "weight_log", "anketa", "clients", "wellbeing"):
        try:
            _ws_rows(sn, _TTL_ADMIN_READ)
        except Exception:
            pass
    bundles = []
    for c in clients:
        try:
            bundles.append((c, _collect_weekly_data(int(c[0]), week_start, week_end)))
        except Exception as e:
            logger.error(f"weekly collect error {c[0]}: {e}")
    sem = asyncio.Semaphore(3)
    period = fmt_period(week_start, week_end)

    async def _one(c, data):
        try:
            uid       = int(c[0])
            user_name = display_name_for(c)
            async with sem:
                report = await asyncio.to_thread(_render_weekly_report, user_name, data)
            if report:
                await safe_send(context, uid,
                                f"📊 *ЕЖЕНЕДЕЛЬНЫЙ ОТЧЁТ*\n"
                                f"_Итоги недели {period}_\n\n{report}")
        except Exception as e:
            logger.error(f"Weekly report error {c[0]}: {e}")

    await asyncio.gather(*(_one(c, d) for c, d in bundles), return_exceptions=True)


async def check_weekly_plan_completion(context: ContextTypes.DEFAULT_TYPE):
    """Пн 06:00 — итоги прошлой недели. Бонус если план закрыт, сброс стрика если нет."""
    today = datetime.now(TIMEZONE).date()
    if today.weekday() != 0:
        return
    week_start, week_end = prev_week_bounds(today)   # пн-вс прошлой недели
    clients    = get_all_clients()
    try:
        w_rows = _ws_rows("workouts", _TTL_SHEET)[1:]
    except Exception:
        w_rows = []
    try:
        anketa_rows = _ws_rows("anketa", _TTL_SHEET)
        name_cache  = {r[0]: r[2] for r in anketa_rows if r and len(r) > 2 and r[2]}
    except Exception:
        name_cache = {}

    for c in clients:
        if not c or not c[0]:
            continue
        user_id = int(c[0])
        planned = planned_workouts_per_week(c)
        if planned <= 0:
            continue
        fact = sum(1 for r in w_rows
                   if len(r) > 1 and r[1] == str(user_id)
                   and _date_in_week(r[0], week_start, week_end))
        name   = name_cache.get(c[0]) or (c[1] if len(c) > 1 else "Боец")
        wk_key = week_start.strftime("%Y-%m-%d")
        try:
            if fact >= planned:
                p, _ = await asyncio.to_thread(get_progress, user_id, True)
                if p["last_wbonus"] != wk_key:
                    await asyncio.to_thread(
                        save_progress, user_id, c[1] or "", c[2] or "",
                        p["streak"], p["max_streak"], p["xp"], p["level"],
                        p["coins"], p["last_workout"], p["last_food"],
                        p["food_streak"], p["food_max_streak"],
                        wk_key, p["last_fbonus"])
                    await add_xp(user_id, c[1] or "", c[2] or "",
                                 XP_STREAK_WEEK, coins_amount=COINS_STREAK_WEEK, context=context)
                    await safe_send(context, user_id,
                                    f"🎉 *{md_safe(name)}, неделя закрыта!*\n\n"
                                    f"*{fact} из {planned}* {plural(planned, 'тренировки', 'тренировок', 'тренировок')} — план выполнен 🔥\n\n"
                                    f"🎁 *+{XP_STREAK_WEEK} XP* · 🪙 *+{COINS_STREAK_WEEK}*\n\n"
                                    "_Братство держит темп. Так и продолжай._ 💪")
            else:
                p, _ = await asyncio.to_thread(get_progress, user_id, True)
                if p["streak"] > 0:
                    # allow_reset=True — единственное место, где стрик обнуляется
                    # осознанно. XP, монеты и рекорды при этом не трогаются.
                    await asyncio.to_thread(
                        save_progress, user_id, c[1] or "", c[2] or "",
                        0, p["max_streak"], p["xp"], p["level"],
                        p["coins"], p["last_workout"], p["last_food"],
                        p["food_streak"], p["food_max_streak"],
                        p["last_wbonus"], p["last_fbonus"], True)
                    await safe_send(context, user_id,
                                    f"💛 *{md_safe(name)}, новая неделя — чистый лист.*\n\n"
                                    f"_Прошлая неделя: {fact} из {planned}. Бывает._\n"
                                    "_На этой — просто начни. Братство рядом._ 💪")
        except Exception as e:
            logger.error(f"check_weekly_plan_completion error {c[0]}: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# СТРАХОВОЧНЫЕ ДЖОБЫ
# ═════════════════════════════════════════════════════════════════════════════
async def replay_pending_ops(context: ContextTypes.DEFAULT_TYPE):
    """Каждые 5 минут добирает начисления, не сохранённые из-за сбоя Sheets.
    Ни одна тренировка и ни один отчёт по питанию не пропадают безвозвратно."""
    ops = await asyncio.to_thread(pending_read_and_clear)
    if not ops:
        return
    logger.info(f"replay_pending_ops: {len(ops)} операций")
    done, failed = 0, 0
    for op in ops:
        try:
            uid = int(op.get("uid"))
        except Exception:
            continue
        xp    = int(op.get("xp", 0) or 0)
        coins = int(op.get("coins", 0) or 0)
        if xp == 0 and coins == 0:
            continue
        ok = await add_xp(uid, op.get("name", ""), op.get("username", ""),
                          xp, coins_amount=coins, context=context)
        if ok is False:
            failed += 1     # add_xp сам вернул операцию в очередь
        else:
            done += 1
            _journal("REPLAY", uid, {"xp": xp, "coins": coins})
        await asyncio.sleep(0.4)
    if done:
        try:
            await safe_send(context, TRAINER_ID,
                            f"🔁 Восстановлено отложенных начислений: *{done}*"
                            + (f" · не удалось: {failed}" if failed else ""))
        except Exception:
            pass


async def integrity_audit(context: ContextTypes.DEFAULT_TYPE):
    """Каждый час сверяет таблицу progress с несгораемыми максимумами.
    Находит обнулённые или пропавшие строки и чинит их сам."""
    try:
        rows = await asyncio.to_thread(_ws_rows, "progress", 5)
    except Exception as e:
        logger.error(f"integrity_audit read: {e}")
        return
    in_sheet = {}
    for r in rows[1:] if rows else []:
        if r and str(r[0]).strip().isdigit():
            in_sheet[str(r[0]).strip()] = _row_to_progress(r)

    repaired = []
    with _HW_LOCK:
        watched = list(_HW.items())
    for uid, hw in watched:
        if int(hw.get("xp", 0) or 0) <= 0:
            continue
        cur = in_sheet.get(uid)
        gap = []
        if cur is None:
            gap.append("строка отсутствует")
        else:
            for f in _MONOTONIC_FIELDS:
                if int(cur.get(f, 0) or 0) < int(hw.get(f, 0) or 0):
                    gap.append(f"{f} {cur.get(f, 0)}<{hw.get(f)}")
        if not gap:
            continue
        try:
            base = cur or {}
            merged = {**PROGRESS_EMPTY, **base}
            for f in _MONOTONIC_FIELDS:
                merged[f] = max(int(merged.get(f, 0) or 0), int(hw.get(f, 0) or 0))
            for f in ("streak", "last_workout", "last_food", "last_wbonus", "last_fbonus"):
                if not merged.get(f) and hw.get(f):
                    merged[f] = hw[f]
            await asyncio.to_thread(
                save_progress, int(uid), "", "",
                merged["streak"], merged["max_streak"], merged["xp"],
                _level_value_for(merged["xp"]), merged["coins"],
                merged["last_workout"], merged["last_food"],
                merged["food_streak"], merged["food_max_streak"],
                merged["last_wbonus"], merged["last_fbonus"])
            repaired.append((uid, ", ".join(gap), merged["xp"]))
            _journal("AUDIT_REPAIR", uid, {"gap": gap, "xp": merged["xp"]})
        except Exception as e:
            logger.error(f"integrity_audit repair {uid}: {e}")
        await asyncio.sleep(0.5)

    await asyncio.to_thread(hw_save, True)

    msgs = []
    if repaired:
        msgs.append("🛡 *Автовосстановление прогресса*\n" + "\n".join(
            f"• `{u}` — {g} → {x} XP" for u, g, x in repaired[:10]))
    if _INTEGRITY_ALERTS:
        msgs.append("⚠️ *События целостности данных:*\n" +
                    "\n".join(f"• {a}" for a in _INTEGRITY_ALERTS[-10:]))
        _INTEGRITY_ALERTS.clear()
    for m in msgs:
        try:
            await safe_send(context, TRAINER_ID, m)
        except Exception:
            pass


def _dump_backup(tag: str = "auto") -> str:
    """Полный снимок всех листов в JSON на диск. Возвращает путь."""
    snapshot = {}
    for sn in ("clients", "progress", "workouts", "food_log", "weight_log",
               "goals", "anketa", "records", "wellbeing", "plan_bonus"):
        try:
            snapshot[sn] = _gs_call(ws(sn).get_all_values, tries=2)
        except Exception as e:
            snapshot[sn] = {"error": str(e)}
    with _HW_LOCK:
        snapshot["_hw"] = dict(_HW)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(
        BACKUP_DIR,
        f"backup_{tag}_{datetime.now(TIMEZONE).strftime('%Y%m%d_%H%M%S')}.json")
    _atomic_write(path, json.dumps(snapshot, ensure_ascii=False))
    # Держим последние 20 снимков
    try:
        files = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("backup_"))
        for old in files[:-20]:
            os.remove(os.path.join(BACKUP_DIR, old))
    except Exception:
        pass
    logger.info(f"backup saved: {path}")
    return path


async def daily_backup_job(context: ContextTypes.DEFAULT_TYPE):
    """03:30 — ежедневный снимок всех данных на диск."""
    try:
        path = await asyncio.to_thread(_dump_backup, "daily")
        logger.info(f"daily backup: {path}")
    except Exception as e:
        logger.error(f"daily_backup_job: {e}")


async def send_monthly_reports_job(context: ContextTypes.DEFAULT_TYPE):
    """1-е число каждого месяца в 10:00 — ежемесячный отчёт."""
    if datetime.now(TIMEZONE).day != 1:
        return
    clients = get_all_clients()
    for c in clients:
        if not c or not c[0]:
            continue
        try:
            await send_monthly_report(context, int(c[0]), c)
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"monthly report error {c[0]}: {e}")


async def send_monthly_report(context, user_id: int, client_row):
    """Генерирует и отправляет ежемесячный AI-отчёт клиенту.
    Полный контекст: профиль, тренировки за месяц, питание, вес, восстановление."""
    try:
        name = get_display_name(user_id, client_row[1] if len(client_row) > 1 else "")
        today = datetime.now(TIMEZONE).date()
        month_start = today.replace(day=1) - timedelta(days=1)  # прошлый месяц
        month_start = month_start.replace(day=1)

        # Полный контекст клиента
        coach_ctx = build_coach_context(user_id)

        # Тренировки за месяц
        w_rows = _ws_rows_safe("workouts", _TTL_ADMIN_READ)
        w_month = [r for r in (w_rows[1:] if w_rows else [])
                   if len(r) > 1 and r[1] == str(user_id)
                   and _safe_date(r[0].split()[0], "%d.%m.%Y")
                   and _safe_date(r[0].split()[0], "%d.%m.%Y") >= month_start]
        total_workouts = len(w_month)

        # Силовые логи за месяц (последние 10)
        strength_lines = []
        for r in w_month[-10:]:
            slog = r[4] if len(r) > 4 else ""
            if slog:
                strength_lines.append(f"{r[0].split()[0]}: {slog[:120]}")

        # Питание за месяц
        f_rows = _ws_rows("food_log", _TTL_ADMIN_READ)
        f_month = [r for r in (f_rows[1:] if f_rows else [])
                   if len(r) > 0 and r[0] == str(user_id)
                   and len(r) > 2 and _safe_date(r[2], "%d.%m.%Y")
                   and _safe_date(r[2], "%d.%m.%Y") >= month_start]
        food_days = len(f_month)

        # Вес — динамика за месяц
        wh = get_user_weight_history(user_id, weeks=8)
        weight_line = ", ".join(f"{d}: {kg}кг" for d, kg in wh[-4:]) if wh else "нет данных"

        # Восстановление за месяц
        wellbeing_trend = _get_wellbeing_trend(user_id, days=30)
        bad_days = sum(1 for r in wellbeing_trend if _wellbeing_score(r) < 3) if wellbeing_trend else 0
        wb_note = f"{bad_days} из {len(wellbeing_trend)} дней — плохое восстановление" if wellbeing_trend else "нет данных"

        # Прогресс
        p, _ = get_progress(user_id)
        level_name, _, _ = get_level_name(p["xp"])

        prompt = (
            f"{coach_ctx}\n\n"
            f"Ежемесячный отчёт для {name}.\n\n"
            f"ТРЕНИРОВКИ ЗА МЕСЯЦ: {total_workouts}\n"
            f"СИЛОВЫЕ ЛОГИ:\n" + ("\n".join(strength_lines) if strength_lines else "нет данных") + "\n\n"
            f"ДНЕЙ ПИТАНИЯ: {food_days}\n"
            f"ДИНАМИКА ВЕСА: {weight_line}\n"
            f"ВОССТАНОВЛЕНИЕ: {wb_note}\n"
            f"РАНГ: {level_name}, XP: {p['xp']}, монеты: {p['coins']}\n"
            f"СТРИК ТРЕНИРОВОК: {p['streak']}, СТРИК ПИТАНИЯ: {p['food_streak']}\n\n"
            "Формат отчёта:\n"
            "📊 *ИТОГИ МЕСЯЦА*\n\n"
            "🏋️ *Тренировки:* (оценка объёма и прогрессии силовых за месяц)\n"
            "🍽️ *Питание:* (стабильность, соответствие норме КБЖУ)\n"
            "⚖️ *Вес:* (динамика за месяц, движение к цели)\n"
            "😴 *Восстановление:* (тренд за месяц)\n"
            "📈 *Главный прогресс:* (1-2 конкретные вещи которые выросли)\n"
            "🎯 *Фокус на следующий месяц:* (3 конкретных приоритета)\n"
            "💪 Мотивирующее завершение под психотип клиента.\n"
        )

        report = await asyncio.to_thread(
            lambda: claude.messages.create(
                model=CLAUDE_MODEL, max_tokens=900,
                system="Ты тренер-нутрициолог. Персональный ежемесячный отчёт. НЕ используй markdown-таблицы (| col |) — Telegram не поддерживает. "
                       "Тепло, конкретно, с учётом профиля клиента.",
                messages=[{"role": "user", "content": prompt}],
            ).content[0].text
        )

        if report:
            await safe_send(context, user_id, report, reply_markup=menu_for(user_id))
    except Exception as e:
        logger.error(f"send_monthly_report error {user_id}: {e}")


async def _get_name_cache():
    try:
        rows = _ws_rows("anketa", _TTL_SHEET)
        return {r[0]: r[2] for r in rows if r and len(r) > 2 and r[2]}
    except Exception:
        return {}


# ── ПУШИ: параллельная отправка ────────────────────────────────────────────────
async def _send_parallel(tasks, batch_size=5):
    """Отправляет пуши пачками по batch_size параллельно.
    Ускоряет отправку 25 клиентам с ~30сек до ~5сек."""
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i+batch_size]
        await asyncio.gather(*batch, return_exceptions=True)
        if i + batch_size < len(tasks):
            await asyncio.sleep(0.3)  # пауза между пачками


async def _build_name_cache():
    try:
        rows = _ws_rows("anketa", _TTL_SHEET)
        return {r[0]: r[2] for r in rows if r and len(r) > 2 and r[2]}
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# 09:00 — УТРО: тренировка или отдых
# ─────────────────────────────────────────────────────────────────────────────
async def send_training_reminders(context: ContextTypes.DEFAULT_TYPE):
    """09:00 — утренний пуш. Тон адаптируется под психотип пользователя."""
    today_abbr = DAY_ABBR[datetime.now(TIMEZONE).weekday()]
    try:
        anketa_rows = _ws_rows("anketa", _TTL_SHEET)
        name_cache  = {r[0]: r[2] for r in anketa_rows if r and len(r) > 2 and r[2]}
    except Exception:
        name_cache = {}

    for c in get_all_clients():
        if not c or not c[0] or not c[0].isdigit():
            continue
        user_id = int(c[0])
        try:
            name    = name_cache.get(c[0]) or (c[1] if len(c) > 1 and c[1] else "Боец")
            days    = get_schedule_days(user_id)
            if not days:
                continue
            phrases = get_psych_phrases(user_id)

            if today_abbr in days:
                emoji, title, subtitle = phrases["morning_train"]
                title_f    = title.format(name=name, xp=XP_PER_WORKOUT)
                subtitle_f = subtitle.format(name=name)
                hist       = get_workout_history(user_id, limit=1)
                prog_hint  = ""
                if hist:
                    slog = hist[-1].get("strength_log", "")
                    if slog:
                        prog_hint = f"\n_Прошлый раз: {slog[:70]}_\n"
                await _safe_push(context, user_id, "morning",
                    f"{emoji} *{md_safe(title_f)}*\n"
                    f"{subtitle_f}"
                    f"{prog_hint}\n\n"
                    f"*+{XP_PER_WORKOUT} XP +{COINS_PER_WORKOUT}* 🪙",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📝 Отчёт за сегодня", callback_data="workout_today")],
                        [InlineKeyboardButton("📅 Отчёт за вчера",   callback_data="workout_yesterday")],
                    ]))
            else:
                emoji, title, subtitle = phrases["morning_rest"]
                title_f    = title.format(name=name)
                subtitle_f = subtitle.format(name=name)
                p, _ = get_progress(user_id)
                fs   = p.get("food_streak", 0)
                streak_line = f"🔥 Стрик питания: *{fs} {plural_days(fs)}* — держим.\n\n" if fs >= 3 else ""
                await _safe_push(context, user_id, "morning",
                    f"{emoji} *{md_safe(title_f)}*\n"
                    f"{subtitle_f}\n\n"
                    f"{streak_line}"
                    f"*+{XP_PER_FOOD_DAY} XP +{COINS_PER_FOOD_DAY}* 🪙",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🥗 Сдать питание", callback_data="food_start_quick")],
                    ]))
        except Exception as e:
            logger.error(f"Training reminder error {c[0]}: {e}")


async def remind_schedule(context: ContextTypes.DEFAULT_TYPE):
    try:
        name_cache = {}
        anketa_rows = _ws_rows("anketa", _TTL_SHEET)
        name_cache  = {r[0]: r[2] for r in anketa_rows if r and len(r) > 2 and r[2]}
    except Exception:
        name_cache = {}
    for c in get_all_clients():
        if not c or not c[0] or is_schedule_filled(c[0]):
            continue
        try:
            user_id = int(c[0])
            name = name_cache.get(c[0]) or (c[1] if len(c) > 1 and c[1] else "Боец")
            await safe_send(context, user_id,
                f"📅 *{md_safe(name)}.*\n\n"
                "1 минута — и я буду знать когда напоминать,\n"
                "а когда дать тебе отдохнуть.\n\n"
                f"*+{XP_SCHEDULE_BONUS} XP* · 🪙 *+{COINS_SCHEDULE}* за расписание.\n\n"
                "👇 *«📅 Расписание»*",
                reply_markup=menu_for(user_id))
        except Exception as e:
            logger.error(f"remind_schedule error {c[0]}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 12:00 — ДЕНЬ: дожим по активности
# ─────────────────────────────────────────────────────────────────────────────
async def send_engagement_push(context: ContextTypes.DEFAULT_TYPE):
    today   = datetime.now(TIMEZONE).date()
    weekday = today.weekday()
    clients = get_all_clients()
    if not clients:
        return

    prog_rows = _ws_rows_safe("progress", _TTL_ADMIN_READ)
    prog_map  = {}
    for r in (prog_rows[1:] if prog_rows else []):
        if r and r[0] and r[0].isdigit():
            prog_map[r[0]] = r

    try:
        anketa_rows = _ws_rows("anketa", _TTL_SHEET)
        name_cache  = {r[0]: r[2] for r in anketa_rows if r and len(r) > 2 and r[2]}
    except Exception:
        name_cache = {}

    def _days_since(s):
        if not s:
            return 999
        try:
            return (today - datetime.strptime(s, "%Y-%m-%d").date()).days
        except Exception:
            return 999

    # Разные фразы по дням — не приедаются
    SKIP_MSGS = [
        ("💤", "{name}, давно не виделись.", "_Не страшно. Сегодня — новый отсчёт._"),
        ("🤝", "{name}, как ты?", "_Пропуск — не конец. Один выход — и стрик снова живёт._"),
        ("💛", "{name}.", "_Сложная неделя бывает у всех. Маленький шаг сегодня — и ты снова в игре._"),
        ("💪", "{name}, братство ждёт.", "_Возвращайся. Без осуждения. Просто вперёд._"),
    ]
    FOOD_SKIP_MSGS = [
        ("🥗", "{name}, как питание?", "_Одно фото или пара строк — и день не ноль._"),
        ("🥩", "{name}.", "_Мышцы строятся на кухне. Что ел сегодня?_"),
        ("💛", "{name}.", "_Не потеряй стрик из-за одного пропуска. 2 минуты — и готово._"),
    ]
    PROFILE_MSGS = [
        ("📋", "{name}, есть пара незаполненных шагов.", "_Займёт 2 минуты — зато план станет точнее._"),
        ("🎯", "{name}.", "_Профиль неполный — значит тренер знает о тебе ещё не всё. Давай исправим._"),
        ("💛", "{name}.", "_Один шаг — и XP уже начислены. Посмотри что осталось._"),
    ]

    for c in clients:
        if not c or not c[0] or not c[0].isdigit():
            continue
        uid     = c[0]
        user_id = int(uid)
        try:
            name = name_cache.get(uid) or (c[1] if len(c) > 1 and c[1] else "Боец")

            anketa   = is_anketa_filled(user_id)
            goals    = is_goals_filled(user_id)
            schedule = is_schedule_filled(user_id)
            records  = get_records_last_date(user_id) is not None

            # Tier 1 — незаполненный профиль
            if not (anketa and goals and schedule and records):
                emoji, title, subtitle = PROFILE_MSGS[user_id % len(PROFILE_MSGS)]
                steps = [
                    (anketa,   "📋 Анкета", f"+{XP_ANKETA_BONUS} XP +{COINS_ANKETA} 🪙", "«📋 Анкета»"),
                    (goals,    "🎯 Цели",   f"+{XP_GOALS_BONUS} XP +{COINS_GOALS} 🪙",   "«🎯 Цели»"),
                    (records,  "🏆 Рекорды",f"каждый кг = +1 XP",                         "«🏆 Рекорды»"),
                    (schedule, "📅 Расписание",f"+{XP_SCHEDULE_BONUS} XP +{COINS_SCHEDULE} 🪙","«📅 Расписание»"),
                ]
                nxt = next(((label, reward, btn) for done, label, reward, btn in steps if not done), None)
                if nxt:
                    label, reward, btn = nxt
                    emoji, title, subtitle = PROFILE_MSGS[user_id % len(PROFILE_MSGS)]
                    title_fmt    = title.format(name=name)
                    subtitle_fmt = subtitle.format(name=name)
                    await _safe_push(context, user_id, "engagement",
                        f"{emoji} *{md_safe(title_fmt)}*\n"
                        f"{subtitle_fmt}\n\n"
                        f"Следующий шаг: *{label}* — {reward}\n\n"
                        f"👇 {btn}",
                        reply_markup=menu_for(user_id))
                continue

            prow        = prog_map.get(uid, [])
            dw          = _days_since(prow[8] if len(prow) > 8 else "")
            df          = _days_since(prow[9] if len(prow) > 9 else "")
            today_train = DAY_ABBR[weekday] in get_schedule_days(user_id)

            # Tier 2 — пропуск тренировки (фраза из психотипа)
            if dw >= 3 and not today_train:
                phrases_p = get_psych_phrases(user_id)
                emoji, title_t, subtitle_t = phrases_p["skip"]
                p, _ = get_progress(user_id)
                xp   = p["xp"]
                level_name, _, next_thr = get_level_name(xp)
                to_next   = (next_thr - xp) if next_thr else 0
                next_name = get_level_name(next_thr)[0] if next_thr else ""
                next_line = f"\nДо *{next_name}*: *{to_next} XP* 💪" if next_thr else ""
                await _safe_push(context, user_id, "engagement",
                    f"{emoji} *{md_safe(title_t.format(name=name, dw=dw))}*\n"
                    f"{subtitle_t.format(name=name, dw=dw)}\n\n"
                    f"👑 {level_name} · {xp} XP{next_line}\n\n"
                    f"*+{XP_PER_WORKOUT} XP +{COINS_PER_WORKOUT}* 🪙",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📝 Отчёт за сегодня", callback_data="workout_today")],
                        [InlineKeyboardButton("📅 Отчёт за вчера",   callback_data="workout_yesterday")],
                    ]))

            # Tier 3 — пропуск питания (фраза из психотипа)
            elif df >= 2:
                phrases_p = get_psych_phrases(user_id)
                emoji, title_f, subtitle_f = phrases_p["food"]
                p, _ = get_progress(user_id)
                fs   = p.get("food_streak", 0)
                streak_line = f"Стрик: *{fs} {plural_days(fs)}* — сохраним?\n\n" if fs > 0 else "\n"
                await _safe_push(context, user_id, "engagement",
                    f"{emoji} *{md_safe(title_f.format(name=name))}*\n"
                    f"{subtitle_f.format(name=name)}\n\n"
                    f"{streak_line}"
                    f"*+{XP_PER_FOOD_DAY} XP +{COINS_PER_FOOD_DAY}* 🪙",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🥗 Сдать питание", callback_data="food_start_quick")],
                    ]))
        except Exception as e:
            logger.error(f"engagement push error {c[0]}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 21:25 — ВЕЧЕР: умный пуш по теме дня
# Пн=XP/рейтинг  Вт=тренировка  Ср=восстановление  Чт=питание
# Пт=рекорды     Сб=братство        Вс=итоги недели
# ─────────────────────────────────────────────────────────────────────────────
_EVENING_TOPICS = {
    0: "xp_rating",    1: "workout_food",
    2: "recovery",     3: "food_kbju",
    4: "records",      5: "pack",
    6: "week_summary",
}

async def send_evening_push(context: ContextTypes.DEFAULT_TYPE):
    today    = datetime.now(TIMEZONE).date()
    weekday  = today.weekday()
    week_num = today.isocalendar()[1]
    topic    = _EVENING_TOPICS[weekday]
    today_str = today.strftime("%d.%m.%Y")

    clients = get_all_clients()
    if not clients:
        return
    tasks_evening = []

    try:
        anketa_rows = _ws_rows("anketa", _TTL_SHEET)
        name_cache  = {r[0]: r[2] for r in anketa_rows if r and len(r) > 2 and r[2]}
    except Exception:
        name_cache = {}

    prog_rows = _ws_rows_safe("progress", _TTL_ADMIN_READ)
    prog_map  = {}
    for r in (prog_rows[1:] if prog_rows else []):
        if r and r[0] and r[0].isdigit():
            prog_map[r[0]] = r

    w_rows = _ws_rows_safe("workouts", _TTL_ADMIN_READ)
    w_data = w_rows[1:] if w_rows else []

    # Пре-рассчитываем стату братства для субботы
    pack_text = None
    if topic == "pack":
        week_start  = today - timedelta(days=weekday)
        total_all   = len(w_data)
        total_week  = sum(1 for r in w_data if len(r) > 0
                          and _safe_date(r[0].split()[0], "%d.%m.%Y")
                          and _safe_date(r[0].split()[0], "%d.%m.%Y") >= week_start)
        today_rows  = [r for r in w_data if len(r) > 0 and r[0].startswith(today_str)]
        total_today = len(today_rows)
        today_ids   = list(dict.fromkeys(r[1] for r in today_rows if len(r) > 1))
        cl_rows     = _ws_rows("clients", _TTL_ADMIN_READ)
        nm          = {c[0]: name_cache.get(c[0], c[1] if len(c) > 1 else "Боец")
                       for c in cl_rows if c and c[0]}
        today_names = [nm.get(uid, "Боец") for uid in today_ids[:4]]
        rest        = total_today - len(today_names)
        PACK_LINES  = [
            "💪 *Братство не спит*", "⚡ *Братство держит темп*",
            "💥 *Братство не останавливается*", "🔥 *Братство в движении*",
        ]
        hdr = PACK_LINES[week_num % len(PACK_LINES)]
        if today_names:
            nl = ", ".join(f"*{md_safe(n)}*" for n in today_names)
            if rest > 0:
                nl += f" +{rest}"
            today_block = f"Сегодня в деле: {nl} 🔥"
        else:
            today_block = "Сегодня пока никто — братство ждёт первого 👀"
        pack_text = (
            f"{hdr}\n"
            f"━━━━━━━━━━━━━\n\n"
            f"🏋️ Всего: *{total_all}* {plural(total_all, 'тренировка', 'тренировки', 'тренировок')}\n"
            f"📅 Эта неделя: *{total_week}*\n"
            f"⚡ Сегодня: *{total_today}*\n\n"
            f"{today_block}\n\n"
            "━━━━━━━━━━━━━\n"
            "_Каждый отчёт — в общую копилку. Братство считает._ 💪"
        )

    for c in clients:
        if not c or not c[0] or not c[0].isdigit():
            continue
        uid     = c[0]
        user_id = int(uid)
        try:
            name    = name_cache.get(uid) or (c[1] if len(c) > 1 and c[1] else "Боец")
            prow    = prog_map.get(uid, [])
            p_xp    = int(prow[5])  if len(prow) > 5  and prow[5]  else 0
            p_coins = int(prow[7])  if len(prow) > 7  and prow[7]  else 0
            p_str   = int(prow[3])  if len(prow) > 3  and prow[3]  else 0
            p_fstr  = int(prow[10]) if len(prow) > 10 and prow[10] else 0
            level_name, _, next_thr = get_level_name(p_xp)

            workout_done = any(len(r) > 1 and r[1] == uid and r[0].startswith(today_str) for r in w_data)
            food_done    = food_reported_today(user_id)

            msg        = None
            push_type  = f"evening_{topic}"

            # ── ПН: XP / рейтинг ─────────────────────────────────────────
            if topic == "xp_rating":
                to_next = (next_thr - p_xp) if next_thr else 0
                # Показываем прогресс к следующему рангу — не место снизу
                xp_bar = ""
                if next_thr:
                    prev_thr = next((t for t in sorted(LEVELS.keys(), reverse=True) if t <= p_xp), 0)
                    span     = next_thr - prev_thr
                    filled   = int(10 * (p_xp - prev_thr) / span) if span > 0 else 0
                    xp_bar   = f"`{'█' * filled}{'░' * (10 - filled)}` {to_next} XP до *{get_level_name(next_thr)[0]}*\n"
                if p_str >= 14:
                    streak_line = f"\n🔥 Стрик *{p_str} {plural_days(p_str)}* — это уже характер."
                elif p_str >= 7:
                    streak_line = f"\n🔥 Стрик *{p_str} {plural_days(p_str)}* — уже привычка, не случайность."
                elif p_str >= 3:
                    streak_line = f"\n🔥 Стрик *{p_str} {plural_days(p_str)}* — хороший старт. Не ломай."
                elif p_str == 0:
                    streak_line = "\n_Стрик пока 0 — одна тренировка сегодня, и счётчик пошёл._"
                else:
                    streak_line = f"\n🔥 Стрик *{p_str} {plural_days(p_str)}* — держим темп."
                # Реакция на вес/цель — добавляем ценность
                cur_w   = get_user_current_weight(user_id)
                tgt_w   = get_user_target_weight(user_id)
                w_line  = ""
                if cur_w and tgt_w:
                    diff = round(abs(cur_w - tgt_w), 2)
                    if diff > 0:
                        direction = "до цели осталось" if cur_w > tgt_w else "до цели набрать"
                        w_line = f"\n⚖️ {cur_w} → *{tgt_w} кг*  ·  _{direction} {diff} кг_"
                    else:
                        w_line = f"\n🎯 Вес *{cur_w} кг* — цель достигнута! 🔥"
                msg = (
                    f"📊 *С новой неделей, {md_safe(name)}!*\n"
                    f"━━━━━━━━━━━━━\n"
                    f"👑 {level_name}  ·  *{p_xp} XP*  ·  🪙 {p_coins}\n"
                    f"{xp_bar}"
                    f"{streak_line}"
                    f"{w_line}\n\n"
                    "_Свежая неделя — свежий старт. Что закроешь первым?_ 💪"
                )

            # ── ВТ: тренировка или питание ───────────────────────────────
            elif topic == "workout_food":
                today_abbr = DAY_ABBR[weekday]
                is_train   = today_abbr in get_schedule_days(user_id)
                if is_train and not workout_done:
                    lw = prow[8] if len(prow) > 8 else ""
                    # Сигнал тренеру при долгом пропуске
                    if lw:
                        try:
                            ds = (today - datetime.strptime(lw, "%Y-%m-%d").date()).days
                            if ds >= 5:
                                await _notify_trainer(context, user_id, name,
                                    f"⚠️ Пропуск *{ds} дней*\n"
                                    f"Последний отчёт: {lw}\n"
                                    "Сегодня тренировочный — не сдал.")
                        except Exception:
                            pass
                    EVENING_W = [
                        ("🌙", "{name}, как тренировка?", "_Ещё не поздно закрыть. Даже короткая — лучше пропуска._"),
                        ("💪", "{name}.", "_Факт важнее идеала. Отчёт принимается сейчас._"),
                        ("🤝", "{name}, всё ок?", "_Если была тренировка — закрой отчёт. Стрик ждёт._"),
                        ("🎯", "{name}.", "_Небольшая тренировка сегодня — и день не ноль._"),
                    ]
                    emoji, t, s = EVENING_W[(user_id + weekday) % len(EVENING_W)]
                    msg = (
                        f"{emoji} *{md_safe(t.format(name=name))}*\n"
                        f"{s.format(name=name)}\n\n"
                        f"👇 *«🏋️ Тренировки»*  ·  *+{XP_PER_WORKOUT} XP +{COINS_PER_WORKOUT}* 🪙"
                    )
                elif workout_done and not food_done:
                    msg = (
                        f"✅ *{md_safe(name)}, тренировка — есть!*\n\n"
                        f"_Добавь питание — и день полностью закрыт._ 🥗\n\n"
                        f"👇 *«🥗 Питание»*  ·  *+{XP_PER_FOOD_DAY} XP +{COINS_PER_FOOD_DAY}* 🪙"
                    )
                elif not food_done:
                    EVENING_F = [
                        ("🥗", "{name}, питание за сегодня?", "_Одно фото или текст — и стрик живёт._"),
                        ("🥩", "{name}.", "_Мышцы работают даже в отдых. Покорми их._"),
                    ]
                    emoji, t, s = EVENING_F[(user_id + weekday) % len(EVENING_F)]
                    streak_note = f"🔥 Стрик питания: *{p_fstr} {plural_days(p_fstr)}*\n\n" if p_fstr >= 3 else "\n"
                    msg = (
                        f"{emoji} *{md_safe(t.format(name=name))}*\n"
                        f"{s.format(name=name)}\n\n"
                        f"{streak_note}"
                        f"👇 *«🥗 Питание»*  ·  *+{XP_PER_FOOD_DAY} XP +{COINS_PER_FOOD_DAY}* 🪙"
                    )

            # ── СР: восстановление ───────────────────────────────────────
            elif topic == "recovery":
                bad_days = 0
                for r in reversed(_get_wellbeing_trend(user_id, days=3)):
                    if _wellbeing_score(r) < 3:
                        bad_days += 1
                    else:
                        break
                if bad_days >= 2:
                    RECOVERY_MSGS = [
                        ("💛", "{name}, как ты?",
                         f"_Несколько дней подряд — усталость и недосып. Сегодня главное — выспаться. Тело скажет спасибо._"),
                        ("😴", "{name}.",
                         f"_{bad_days} дня — сон и энергия не на высоте. Если сегодня тренировка — снизь нагрузку, не геройствуй._"),
                        ("🌿", "{name}.",
                         "_Восстановление — это не слабость. Это часть роста. Хороший сон сегодня = сильная тренировка завтра._"),
                    ]
                    emoji, t, s = RECOVERY_MSGS[(user_id + weekday) % len(RECOVERY_MSGS)]
                    msg = (
                        f"{emoji} *{md_safe(t.format(name=name))}*\n"
                        f"{s.format(name=name)}"
                    )
                else:
                    nt = compute_nutrition_targets(user_id)
                    if nt and not food_done:
                        msg = (
                            f"😴 *{md_safe(name)}, как восстановление?*\n\n"
                            f"_Питание — половина прогресса. Норма сегодня: *{nt['kcal']} ккал* · белок *{nt['protein']} г*_\n\n"
                            f"👇 *«🥗 Питание»*"
                        )
                    else:
                        msg = (
                            f"💚 *{md_safe(name)}, как ощущения?*\n\n"
                            "_Завтра перед тренировкой ответь на 3 вопроса о самочувствии — займёт 30 секунд._\n"
                            "_Тренер подберёт нагрузку точнее._ 💪"
                        )

            # ── ЧТ: питание + КБЖУ ──────────────────────────────────────
            elif topic == "food_kbju":
                nt = compute_nutrition_targets(user_id)
                if not food_done:
                    FOOD_EVE = [
                        ("🥗", "{name}, питание за сегодня?",
                         "_Один отчёт — и стрик живёт. Фото или текст._"),
                        ("📊", "{name}.",
                         "_КБЖУ за день — это 2 минуты. Зато видна картина._"),
                        ("🥩", "{name}, белок набрал?",
                         "_Без белка мышцы не растут. Проверь норму._"),
                    ]
                    emoji, t, s = FOOD_EVE[(user_id + weekday) % len(FOOD_EVE)]
                    nt_block = ""
                    if nt:
                        nt_block = f"_Норма: *{nt['kcal']} ккал* · белок *{nt['protein']} г* · {nt['mode']}_\n\n"
                    streak_note = f"🔥 Стрик: *{p_fstr} {plural_days(p_fstr)}*\n\n" if p_fstr >= 3 else "\n"
                    msg = (
                        f"{emoji} *{md_safe(t.format(name=name))}*\n"
                        f"{s.format(name=name)}\n\n"
                        f"{nt_block}"
                        f"{streak_note}"
                        f"👇 *«🥗 Питание»*  ·  *+{XP_PER_FOOD_DAY} XP +{COINS_PER_FOOD_DAY}* 🪙"
                    )
                else:
                    if nt:
                        msg = (
                            f"✅ *{md_safe(name)}, питание закрыто — отлично!*\n\n"
                            f"_Белок *{nt['protein']} г* — именно он строит мышцы пока ты спишь._ 💪"
                        )
                    else:
                        msg = (
                            f"✅ *{md_safe(name)}, питание закрыто!*\n\n"
                            "_Заполни анкету — посчитаю твою норму КБЖУ персонально._\n"
                            "👇 *«📋 Анкета»*"
                        )

            # ── ПТ: рекорды ─────────────────────────────────────────────
            elif topic == "records":
                rec           = get_last_records(user_id)
                last_rec_date = get_records_last_date(user_id)
                if rec and rec.get("sum") not in (None, "", "0"):
                    days_ago  = (today - last_rec_date).days if last_rec_date else 999
                    days_left = RECORDS_SEASON_DAYS - days_ago
                    if days_left > 0:
                        REC_MSGS = [
                            (f"🏆 *{md_safe(name)}, твои силовые:*\n\n"
                             f"🏋️ Присед *{rec['squat']} кг*  ·  💪 Жим *{rec['bench']} кг*  ·  🔥 Тяга *{rec['deadlift']} кг*\n"
                             f"_Сумма: *{rec['sum']} кг* — обновим через {days_left} {plural_days(days_left)}._"),
                            (f"💪 *{md_safe(name)}.*\n\n"
                             f"_Лучший присед: *{rec['squat']} кг*. Лучший жим: *{rec['bench']} кг*._\n"
                             f"_Цель на следующий сезон — прибавить хотя бы 5 кг к чему-то одному._ 🎯"),
                        ]
                        msg = REC_MSGS[(user_id + week_num) % len(REC_MSGS)]
                    else:
                        msg = (
                            f"🏆 *{md_safe(name)}, сезон открыт!*\n\n"
                            "_3 месяца прошло — самое время замерить прогресс._\n"
                            "Каждый кг = +1 XP прямо сейчас.\n\n"
                            "👇 *«🏆 Рекорды»*"
                        )
                else:
                    msg = (
                        f"🏆 *{md_safe(name)}.*\n\n"
                        "_Рекорды пока не заполнены._\n"
                        "Присед 100 + жим 80 + тяга 120 = *+300 XP* — 5 минут.\n\n"
                        "👇 *«🏆 Рекорды»*"
                    )

            # ── СБ: братство + напоминание про завтрашнее взвешивание ──────
            elif topic == "pack":
                msg = pack_text
                if msg:
                    msg += "\n\n_Завтра воскресенье — взвесься утром натощак._ ⚖️"

            # ── ВС: итоги недели ────────────────────────────────────────
            elif topic == "week_summary":
                week_start  = today - timedelta(days=weekday)
                w_this_week = sum(1 for r in w_data
                                  if len(r) > 1 and r[1] == uid
                                  and _safe_date(r[0].split()[0], "%d.%m.%Y")
                                  and _safe_date(r[0].split()[0], "%d.%m.%Y") >= week_start)
                planned     = planned_workouts_per_week(c)
                # Реакция зависит от результата — не стыдим, вдохновляем
                if planned and w_this_week >= planned:
                    result_line = f"✅ *{w_this_week} из {planned}* {plural(planned, 'тренировки', 'тренировок', 'тренировок')} — план закрыт 🔥"
                elif planned and w_this_week > 0:
                    result_line = f"💪 *{w_this_week} из {planned}* — уже что-то. На следующей неделе добавим."
                elif planned:
                    result_line = f"_Эта неделя не задалась — и это нормально. Следующая — чистый лист._"
                else:
                    result_line = f"🏋️ За неделю: *{w_this_week}* {plural(w_this_week, 'тренировка', 'тренировки', 'тренировок')}"
                wh = get_user_weight_history(user_id, weeks=4)
                weight_line = ""
                if len(wh) >= 2:
                    diff = wh[-1][1] - wh[-2][1]
                    sign = "+" if diff >= 0 else ""
                    weight_line = f"\n⚖️ Вес: {wh[-2][1]} → *{wh[-1][1]} кг* ({sign}{diff} кг)"
                streak_note = ""
                if p_str >= 7:
                    streak_note = f"\n🔥 Стрик *{p_str} {plural_days(p_str)}* — это уже характер."
                elif p_str >= 3:
                    streak_note = f"\n🔥 Стрик *{p_str} {plural_days(p_str)}* — держим."
                msg = (
                    f"📊 *{md_safe(name)}, неделя прошла.*\n\n"
                    f"{result_line}"
                    f"{weight_line}"
                    f"{streak_note}\n\n"
                    f"Утром натощак взвесься 👇\n"
                    f"*«⚖️ Измерить вес»*  ·  *+{XP_WEIGH_IN} XP +{COINS_WEIGH_IN}* 🪙"
                )

            if msg:
                tasks_evening.append(
                    _safe_push(context, user_id, push_type, msg,
                               reply_markup=menu_for(user_id)))

        except Exception as e:
            logger.error(f"evening push error {c[0]} topic={topic}: {e}")

    await _send_parallel(tasks_evening)


# ─────────────────────────────────────────────────────────────────────────────
# 08:00 вс — напоминание взвеситься
# ─────────────────────────────────────────────────────────────────────────────
async def send_weighin_reminders(context: ContextTypes.DEFAULT_TYPE):
    if datetime.now(TIMEZONE).weekday() != 6:
        return
    try:
        anketa_rows = _ws_rows("anketa", _TTL_SHEET)
        name_cache  = {r[0]: r[2] for r in anketa_rows if r and len(r) > 2 and r[2]}
    except Exception:
        name_cache = {}
    WEIGH_MSGS = [
        ("☀️", "{name}, доброе воскресенье!", "_Утром натощак — самые честные цифры._"),
        ("⚖️", "{name}!", "_Воскресенье — день замера. Посмотрим динамику._"),
        ("📊", "Привет, {name}!", "_Раз в неделю — и видна вся картина. Взвешивайся._"),
    ]
    week_num = datetime.now(TIMEZONE).date().isocalendar()[1]
    for c in get_all_clients():
        if not c or not c[0]:
            continue
        try:
            user_id = int(c[0])
            name    = name_cache.get(c[0]) or (c[1] if len(c) > 1 and c[1] else "Боец")
            emoji, title, subtitle = WEIGH_MSGS[week_num % len(WEIGH_MSGS)]
            title    = title.format(name=name)
            subtitle = subtitle.format(name=name)
            wh = get_user_weight_history(user_id, weeks=4)
            weight_note = ""
            if len(wh) >= 2:
                diff = wh[-1][1] - wh[-2][1]
                sign = "+" if diff >= 0 else ""
                weight_note = f"_Прошлый раз: {wh[-1][1]} кг ({sign}{diff} кг)_\n\n"
            elif wh:
                weight_note = f"_Прошлый раз: {wh[-1][1]} кг_\n\n"
            await _safe_push(context, user_id, "weighin",
                f"{emoji} *{md_safe(title.format(name=name))}*\n"
                f"{subtitle.format(name=name)}\n\n"
                f"{weight_note}"
                f"👇 *«⚖️ Измерить вес»*  ·  *+{XP_WEIGH_IN} XP +{COINS_WEIGH_IN}* 🪙",
                reply_markup=menu_for(user_id))
        except Exception as e:
            logger.error(f"Weigh-in reminder error {c[0]}: {e}")


async def force_import_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/force_import <user_id> — ручной перенос прогресса из прогрева. Только тренер."""
    if update.effective_user.id != TRAINER_ID:
        return
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            "Использование: `/force_import <user_id>`",
            parse_mode="Markdown",
        )
        return
    target_id = int(args[0])
    data      = get_warmup_transfer(target_id)
    if not data:
        await update.message.reply_text(f"❌ Нет данных в warmup_transfer для `{target_id}`.",
                                        parse_mode="Markdown")
        return
    xp             = data["xp"]
    coins_credited = data["coins"]
    answers        = data.get("answers", {})
    already        = warmup_already_imported(target_id)
    p_before, _    = get_progress(target_id)
    await update.message.reply_text(
        f"📋 *warmup_transfer для {target_id}:*\n"
        f"XP: {xp} · Монеты: {coins_credited} · Ответов: {len(answers)}\n"
        f"Сейчас: XP={p_before['xp']}, монеты={p_before['coins']}\n"
        f"Импортирован: {'✅' if already else '❌'}\n\nНачисляю...",
        parse_mode="Markdown",
    )
    try:
        cl       = next((c for c in get_all_clients() if c and c[0] == str(target_id)), None)
        name     = display_name_for(cl) if cl else str(target_id)
        username = cl[2] if cl and len(cl) > 2 else str(target_id)
        await add_xp(target_id, name, username, xp, coins_credited, context=context)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка add_xp: {e}")
        return
    if not already:
        mark_warmup_imported(target_id, xp, coins_credited)
    cl = next((c for c in get_all_clients() if c and c[0] == str(target_id)), None)
    username = cl[2] if cl and len(cl) > 2 else str(target_id)
    save_warmup_answers(target_id, username, answers)
    _prefill_anketa_from_warmup(target_id, username, answers)
    p_after, _ = get_progress(target_id)
    await update.message.reply_text(
        f"✅ *Готово для {target_id}*\n\n"
        f"XP: {p_before['xp']} → {p_after['xp']} (+{xp})\n"
        f"Монеты: {p_before['coins']} → {p_after['coins']} (+{coins_credited})",
        parse_mode="Markdown",
    )
    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🔥 *Прогресс из прогрева перенесён!*\n\n⚡ +{xp} XP · 🪙 +{coins_credited}\n\nЖми /start 💪",
            parse_mode="Markdown",
        )
    except Exception:
        pass


async def send_trainer_weekly_report(context: ContextTypes.DEFAULT_TYPE):
    """Пн 07:30 — автоматический отчёт тренеру с аналитикой и рисками."""
    if datetime.now(TIMEZONE).weekday() != 0:
        return
    try:
        clients   = get_all_clients()
        if not clients:
            return
        today = datetime.now(TIMEZONE).date()
        # Календарная прошлая неделя пн-вс — тот же период, что и у клиентов
        wk_start, wk_end = prev_week_bounds(today)

        prog_rows = _ws_rows_safe("progress", _TTL_ADMIN_READ)
        prog_map  = {}
        for r in (prog_rows[1:] if prog_rows else []):
            if r and r[0] and r[0].isdigit():
                prog_map[r[0]] = r

        w_rows = _ws_rows_safe("workouts", _TTL_ADMIN_READ)
        w_data = w_rows[1:] if w_rows else []

        total      = len(clients)
        active_w   = 0
        total_work = 0
        risks_c    = []
        risks_w    = []

        try:
            anketa_rows = _ws_rows("anketa", _TTL_SHEET)
            name_cache  = {r[0]: r[2] for r in anketa_rows if r and len(r) > 2 and r[2]}
        except Exception:
            name_cache = {}

        for c in clients:
            if not c or not c[0] or not c[0].isdigit():
                continue
            uid  = c[0]
            pr   = prog_map.get(uid, [])
            lw   = pr[8] if len(pr) > 8 else ""
            lf   = pr[9] if len(pr) > 9 else ""
            best = 999
            for s in (lw, lf):
                if s:
                    try:
                        d = (today - datetime.strptime(s, "%Y-%m-%d").date()).days
                        best = min(best, d)
                    except Exception:
                        pass
            if best <= 7:
                active_w += 1
            # Тренировки за прошлую календарную неделю
            wk_count = sum(1 for r in w_data
                           if len(r) > 1 and r[1] == uid and r[0]
                           and _date_in_week(r[0].split()[0], wk_start, wk_end))
            total_work += wk_count

            # Риск
            risk = compute_churn_risk(int(uid))
            name = name_cache.get(uid) or (c[1] if len(c) > 1 else "Боец")
            if risk["level"] == "critical":
                risks_c.append((name, uid, risk["reasons"]))
            elif risk["level"] == "warning":
                risks_w.append((name, uid, risk["reasons"]))

        avg_work = total_work / total if total else 0
        pct      = int(active_w / total * 100) if total else 0

        text = (
            f"📊 *ПОНЕДЕЛЬНИК — ДАШБОРД БРАТСТВА*\n"
            f"_Итоги недели {fmt_period(wk_start, wk_end)}_\n"
            f"━━━━━━━━━━━━━\n\n"
            f"👥 Всего: *{total}* · Активны: *{active_w}* ({pct}%)\n"
            f"🏋️ Тренировок за неделю: *{total_work}* (avg {avg_work:.1f}/чел)\n\n"
        )
        if risks_c:
            text += f"🔴 *Критично ({len(risks_c)}) — написать сегодня:*\n"
            for name, uid, reasons in risks_c[:7]:
                r_str = ", ".join(reasons[:2]) if reasons else "нет данных"
                text += f"• *{md_safe(name)}* `{uid}` — {r_str}\n"
        if risks_w:
            text += f"\n🟡 *Под наблюдением ({len(risks_w)}):*\n"
            for name, uid, reasons in risks_w[:5]:
                r_str = reasons[0] if reasons else ""
                text += f"• *{md_safe(name)}* — {r_str}\n"
        if not risks_c and not risks_w:
            text += "✅ Все клиенты активны — рисков нет.\n"

        text += "\n_Полный дашборд: /dashboard_"

        await safe_send(context, TRAINER_ID, text)
    except Exception as e:
        logger.error(f"send_trainer_weekly_report error: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# КОМАНДЫ ТРЕНЕРА ДЛЯ РАБОТЫ С ДАННЫМИ
# ═════════════════════════════════════════════════════════════════════════════
async def journal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/journal <user_id> [N] — последние N изменений прогресса подопечного."""
    if update.effective_user.id != TRAINER_ID:
        return
    args = context.args or []
    if not args:
        await safe_reply(update,
                         "Использование: `/journal <user_id> [сколько]`\n"
                         "Показывает историю изменений XP по этому человеку.")
        return
    uid   = str(args[0]).strip()
    limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 15

    def _read():
        if not os.path.exists(JOURNAL_FILE):
            return []
        out = []
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("uid") == uid:
                    out.append(rec)
        return out[-limit:]

    recs = await asyncio.to_thread(_read)
    if not recs:
        await safe_reply(update, f"По `{uid}` записей в журнале нет.")
        return
    lines = [f"📓 *Журнал прогресса* `{uid}`\n"]
    for r in recs:
        ev = r.get("ev")
        if ev == "SAVE":
            lines.append(f"`{r['ts']}` SAVE — {r.get('xp')} XP · "
                         f"{r.get('coins')}🪙 · стрик {r.get('streak')}")
        elif ev == "BLOCKED_DECREASE":
            lines.append(f"`{r['ts']}` 🛡 блок: {r.get('field')} "
                         f"{r.get('attempt')} → оставлено {r.get('kept')}")
        elif ev in ("HEAL", "AUDIT_REPAIR", "ROW_LOST"):
            lines.append(f"`{r['ts']}` ♻️ {ev} — {r.get('losses') or r.get('gap') or ''}")
        else:
            lines.append(f"`{r['ts']}` {ev}")
    hw = hw_get(uid)
    if hw:
        lines.append(f"\n🛡 *Несгораемый максимум:* {hw.get('xp', 0)} XP · "
                     f"{hw.get('coins', 0)}🪙 · макс. стрик {hw.get('max_streak', 0)}")
    await safe_reply(update, "\n".join(lines)[:3900])


async def restore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/restore <user_id> <xp> [монеты] [стрик] — ручное восстановление."""
    if update.effective_user.id != TRAINER_ID:
        return
    args = context.args or []
    if len(args) < 2:
        await safe_reply(update,
                         "Использование: `/restore <user_id> <xp> [монеты] [стрик]`\n\n"
                         "Поднимает прогресс до указанных значений "
                         "(уменьшить нельзя — защита).")
        return
    try:
        uid   = int(args[0])
        xp    = int(args[1])
        coins = int(args[2]) if len(args) > 2 else 0
        strk  = int(args[3]) if len(args) > 3 else 0
    except ValueError:
        await safe_reply(update, "Числа, пожалуйста: `/restore 123456 1500 400`")
        return

    def _do():
        p, _ = get_progress(uid)
        return save_progress(
            uid, "", "",
            max(strk, p.get("streak", 0)), max(strk, p.get("max_streak", 0)),
            max(xp, p.get("xp", 0)), _level_value_for(max(xp, p.get("xp", 0))),
            max(coins, p.get("coins", 0)), p.get("last_workout", ""),
            last_food=p.get("last_food", ""),
            food_streak=p.get("food_streak", 0),
            food_max_streak=p.get("food_max_streak", 0),
            last_wbonus=p.get("last_wbonus", ""),
            last_fbonus=p.get("last_fbonus", ""))

    try:
        new = await asyncio.to_thread(_do)
        _journal("MANUAL_RESTORE", uid, {"xp": new["xp"], "coins": new["coins"]})
        lvl = get_level_name(new["xp"])[0]
        await safe_reply(update,
                         f"✅ Прогресс `{uid}` восстановлен:\n"
                         f"💎 *{new['xp']} XP* · 🪙 {new['coins']} · "
                         f"🔥 стрик {new['streak']}\n👑 {lvl}")
    except Exception as e:
        logger.error(f"restore_cmd: {e}")
        await safe_reply(update, f"⚠️ Не получилось: {e}")


async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/backup — снимок всех данных на диск прямо сейчас."""
    if update.effective_user.id != TRAINER_ID:
        return
    await safe_reply(update, "⏳ Делаю снимок всех таблиц...")
    try:
        path = await asyncio.to_thread(_dump_backup, "manual")
        size = os.path.getsize(path) // 1024
        await safe_reply(update, f"💾 Готово: `{path}` ({size} КБ)")
    except Exception as e:
        await safe_reply(update, f"⚠️ Ошибка: {e}")


async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/health — состояние защиты данных."""
    if update.effective_user.id != TRAINER_ID:
        return
    with _HW_LOCK:
        n_hw   = len(_HW)
        top_xp = sorted((int(v.get("xp", 0) or 0) for v in _HW.values()), reverse=True)[:3]
    pend = 0
    try:
        if os.path.exists(PENDING_FILE):
            with open(PENDING_FILE, "r", encoding="utf-8") as f:
                pend = sum(1 for l in f if l.strip())
    except Exception:
        pass
    try:
        rows = await asyncio.to_thread(_ws_rows, "progress", 5)
        sheet_n = max(0, len(rows) - 1)
        sheet_s = f"✅ доступна, строк: {sheet_n}"
    except Exception as e:
        sheet_s = f"⚠️ недоступна: {e}"
    ws_, we_ = prev_week_bounds()
    await safe_reply(update,
                     "🛡 *Состояние защиты данных*\n\n"
                     f"📊 Лист progress: {sheet_s}\n"
                     f"🔒 Несгораемых марок: *{n_hw}*"
                     + (f" (топ XP: {', '.join(map(str, top_xp))})" if top_xp else "") + "\n"
                     f"🔁 Отложенных начислений: *{pend}*\n"
                     f"⚠️ Событий целостности в очереди: *{len(_INTEGRITY_ALERTS)}*\n\n"
                     f"📅 Отчётная неделя для понедельника: *{fmt_period(ws_, we_)}*\n"
                     f"💾 Резервные копии: `{BACKUP_DIR}`")


async def post_init(app: Application):
    load_states()
    hw_load()
    # Первичное наполнение несгораемых марок из таблицы:
    # если файл HW пуст (первый запуск после обновления) — берём текущие значения
    try:
        rows = await asyncio.to_thread(_ws_rows, "progress", 1)
        for r in (rows[1:] if rows else []):
            if r and str(r[0]).strip().isdigit():
                hw_update(str(r[0]).strip(), _row_to_progress(r))
        hw_save(True)
        logger.info("HW-марки синхронизированы с таблицей")
    except Exception as e:
        logger.error(f"HW warmup error: {e}")
    await app.bot.set_my_commands([
        BotCommand("start",    "🚀 Старт / главное меню"),
        BotCommand("stats",    "📊 Моя статистика"),
        BotCommand("workout",  "🏋️ Тренировка (+20 XP)"),
        BotCommand("food",     "🥗 Питание (+10 XP)"),
        BotCommand("schedule", "📅 Расписание"),
        BotCommand("goals",    "🎯 Цели"),
        BotCommand("records",  "🏆 Рекорды (1 кг = 1 XP)"),
        BotCommand("weighin",  "⚖️ Измерить вес (вс)"),
        BotCommand("top",      "💪 Топ-5 братства"),
        BotCommand("anketa",   "📋 Анкета"),
        BotCommand("dashboard","📊 Дашборд (тренер)"),
    ])
    try:
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception as e:
        logger.error(f"set_chat_menu_button error: {e}")
    try:
        await app.bot.set_my_short_description(
            "💪 Братство протрузии. Здесь не болтают — здесь делают. Жми «Старт»"
        )
        await app.bot.set_my_description(
            "💪 БРАТСТВО ПРОТРУЗИИ — Территория прогресса\n\n"
            "Здесь не болтают. Здесь делают.\n"
            "Братство уже в движении — ждём тебя.\n\n"
            f"🏋️ Тренировка → +{XP_PER_WORKOUT} XP\n"
            f"🥩 Питание → +{XP_PER_FOOD_DAY} XP\n"
            f"⚖️ Вес (Вс) → +{XP_WEIGH_IN} XP\n"
            "🏆 Рекорды → +1 XP за каждый кг\n\n"
            "Путь: 🫡 Протрузианец → ⚔️ Адепт → 🏋️ Лифтер → 💎 Титан → 🤖 Киборг → 👑 Легенда\n\n"
            "Жми «Старт» — и в бой 🔥"
        )
    except Exception as e:
        logger.error(f"set description error: {e}")


async def post_stop(app: Application):
    save_states()
    hw_save(True)


async def flush_states_job(context: ContextTypes.DEFAULT_TYPE):
    save_states()
    hw_save()


async def consult_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != TRAINER_ID:
        return
    uid = query.data.replace("consult_view_", "")
    try:
        rows = ws("consultation_requests").get_all_values()[1:]
        row  = next((r for r in rows if r and r[0] == uid), None)
        if not row:
            await query.message.reply_text("⚠️ Данные консультации не найдены.")
            return
        first_name = row[1] if len(row) > 1 else "—"
        username   = row[2] if len(row) > 2 else "—"
        date       = row[6] if len(row) > 6 else "—"
        answers    = json.loads(row[5]) if len(row) > 5 and row[5] else {}
        LABELS = {
            "c_name": "Имя", "c_age": "Возраст", "c_height": "Рост", "c_weight": "Вес",
            "c_target": "Цель", "c_health": "Здоровье", "c_goal": "Главная цель",
            "c_block": "Что мешало", "c_hours": "Часов/нед", "c_place": "Место тренировок",
            "c_level": "Уровень подготовки", "c_food_day": "День питания",
            "c_food_hard": "Сложно в питании", "c_sleep": "Сон",
            "c_time": "Время тренировок", "c_why": "Ради чего",
            "c_block2": "Что удерживает", "c_result": "Результат 3 мес",
            "c_readiness": "Готовность (1-10)", "c_question": "Вопрос тренеру",
        }
        lines = [f"📋 *АНКЕТА — {md_safe(first_name)}* (@{md_safe(username)})",
                 f"🆔 `{uid}`  •  {date}", ""]
        for key, label in LABELS.items():
            val = answers.get(key, "")
            if val:
                lines.append(f"• *{label}:* {md_safe(val)}")
        text = "\n".join(lines)
        for start in range(0, len(text), 3800):
            await query.message.reply_text(text[start:start + 3800], parse_mode="Markdown")
    except Exception as e:
        logger.error(f"consult_view_callback error: {e}")
        await query.message.reply_text("⚠️ Ошибка при загрузке анкеты.")


# ── CALLBACK ROUTER ───────────────────────────────────────────────────────────
async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обёртка: гарантирует, что кнопка всегда «отпускается».

    Раньше при исключении внутри ветки роутера пользователь видел вечный
    спиннер и ничего больше — именно так проявлялся баг с отчётом
    о тренировке. Теперь любая ошибка гасит спиннер и даёт понятный ответ.
    """
    query = update.callback_query
    try:
        await _callback_router_impl(update, context)
    except Exception as e:
        logger.error(f"callback_router error data={query.data!r}: {e}", exc_info=True)
        try:
            await query.answer("Что-то пошло не так, попробуй ещё раз")
        except Exception:
            pass
        try:
            await safe_send(context, query.from_user.id,
                            "⚠️ Не получилось обработать нажатие.\n"
                            "Попробуй ещё раз или открой раздел через меню 👇",
                            reply_markup=menu_for(query.from_user.id))
        except Exception:
            pass


async def _callback_router_impl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data.startswith("sched_"):
        await schedule_callback(update, context)
    elif data == "weighin_cancel":
        query = update.callback_query
        await query.answer()
        weighin_states.pop(query.from_user.id, None)
        save_states()
        await query.edit_message_text("❌ Отменено.")
        await context.bot.send_message(chat_id=query.from_user.id,
                                       text="Главное меню:",
                                       reply_markup=menu_for(query.from_user.id))
    elif data == "feedback_ack":
        query = update.callback_query
        await query.answer("👍 Принято!")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await safe_send(context, query.from_user.id, "💪", reply_markup=menu_for(query.from_user.id))
    elif data == "food_start_quick":
        query = update.callback_query
        await query.answer()
        user  = query.from_user
        food_states[user.id] = {"mode": "collecting", "meals": [], "photos": []}
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await safe_send(context, user.id,
            "🥗 *Отчёт по питанию*\n\n"
            "Отправь весь свой рацион!\n"
            "За вчера или сегодня…\n\n"
            "📸 Фото · 📝 Текст · 📸+📝 Фото с подписями\n\n"
            "_Когда добавишь всё — жми «Готово»._",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Готово — анализируй!", callback_data="food_done")],
                [InlineKeyboardButton("❌ Отмена",               callback_data="food_cancel")],
            ]))
    elif data == "shop_training":
        query = update.callback_query
        await query.answer()
        user  = query.from_user
        p, _  = await asyncio.to_thread(get_progress, user.id)
        coins = p["coins"]
        cost  = 700
        await query.message.reply_text(
            "🏋️ *Онлайн-тренировка с тренером — 1 час*\n"
            "━━━━━━━━━━━━━\n\n"
            "Что получишь:\n"
            "• Персональная тренировка по видеосвязи\n"
            "• Разбор техники под твои цели и ограничения\n"
            "• Программа на следующую неделю\n"
            "• Ответы на все вопросы по тренировкам\n\n"
            f"Стоимость: *{cost} 🪙*\n"
            f"У тебя: *{coins} 🪙*\n\n"
            + (f"_Готов потратить {cost} монет?_"
               if coins >= cost
               else f"_Не хватает {cost - coins} монет. Копим дальше на мощные бонусы!_ 💪"),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✅ Купить за {cost} 🪙", callback_data="shop_buy_training")],
                [InlineKeyboardButton("🔙 Назад", callback_data="shop_back")],
            ]) if coins >= cost else InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Копим дальше", callback_data="shop_back")],
            ]),
        )
    elif data == "shop_consult":
        query = update.callback_query
        await query.answer()
        user  = query.from_user
        p, _  = await asyncio.to_thread(get_progress, user.id)
        coins = p["coins"]
        cost  = 500
        await query.message.reply_text(
            "💬 *Личная консультация с тренером — 40 мин*\n"
            "━━━━━━━━━━━━━\n\n"
            "Что получишь:\n"
            "• Разбор твоего прогресса за месяц\n"
            "• Корректировка питания и нагрузки\n"
            "• Ответы на все вопросы по форме и здоровью\n"
            "• План действий на следующий месяц\n\n"
            f"Стоимость: *{cost} 🪙*\n"
            f"У тебя: *{coins} 🪙*\n\n"
            + (f"_Готов потратить {cost} монет?_"
               if coins >= cost
               else f"_Не хватает {cost - coins} монет. Копим дальше!_ 💪"),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✅ Купить за {cost} 🪙", callback_data="shop_buy_consult")],
                [InlineKeyboardButton("🔙 Назад", callback_data="shop_back")],
            ]) if coins >= cost else InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Копим дальше", callback_data="shop_back")],
            ]),
        )
    elif data == "shop_buy_training":
        query = update.callback_query
        user  = query.from_user
        p, _  = await asyncio.to_thread(get_progress, user.id)
        if p["coins"] < 700:
            await query.answer("Не хватает монет!", show_alert=True)
            return
        await query.answer()
        await add_xp(user.id, user.first_name, "", 0, coins_amount=-700, context=context)
        await _notify_trainer(context, user.id,
            get_display_name(user.id, user.first_name),
            "🏋️ *Купил онлайн-тренировку за 700 🪙!*\n\n_Свяжись с ним для записи._")
        await query.edit_message_text(
            "✅ *Тренировка куплена!*\n\n"
            "Тренер получил уведомление и скоро свяжется с тобой для записи.\n\n"
            "_Спасибо за доверие._ 💪",
            parse_mode="Markdown")
    elif data == "shop_buy_consult":
        query = update.callback_query
        user  = query.from_user
        p, _  = await asyncio.to_thread(get_progress, user.id)
        if p["coins"] < 500:
            await query.answer("Не хватает монет!", show_alert=True)
            return
        await query.answer()
        await add_xp(user.id, user.first_name, "", 0, coins_amount=-500, context=context)
        await _notify_trainer(context, user.id,
            get_display_name(user.id, user.first_name),
            "💬 *Купил личную консультацию за 500 🪙!*\n\n_Свяжись с ним для записи._")
        await query.edit_message_text(
            "✅ *Консультация куплена!*\n\n"
            "Тренер получил уведомление и скоро свяжется для записи.\n\n"
            "_Спасибо за доверие._ 💪",
            parse_mode="Markdown")
    elif data == "shop_back":
        query = update.callback_query
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    elif data == "weighin_set_target":
        query = update.callback_query
        await query.answer()
        user  = query.from_user
        cur   = get_user_target_weight(user.id)
        cur_str = f"Текущая цель: *{cur} кг*\n\n" if cur else ""
        weighin_states[user.id] = {"mode": "set_target"}
        save_states()
        await query.message.reply_text(
            f"🎯 *Новая цель по весу*\n\n{cur_str}"
            "Напиши желаемый вес в кг.\n"
            "_Например: 78 или 78.5_",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard())
    elif data.startswith("goal_"):
        await goals_callback(update, context)
    elif data.startswith("ank_"):
        await anketa_callback(update, context)
    elif data.startswith("food_"):
        await food_done_callback(update, context)
    elif data in ("wk_today", "wk_yesterday", "wk_today_go", "wk_yesterday_go",
                  "workout_today", "workout_yesterday"):
        # ⚠️ Объекты Telegram в PTB v20+ заморожены — подменять query.data
        # нельзя (AttributeError, кнопка «крутится» и ничего не происходит).
        # Передаём день и флаг обычными аргументами.
        day  = "yesterday" if "yesterday" in data else "today"
        skip = data.endswith("_go")      # пришёл сразу после опроса самочувствия
        await workout_day_callback(update, context, day=day, skip_wellbeing=skip)
    elif data == "workout_append":
        await workout_append_callback(update, context)
    elif data.startswith("workout_"):
        await workout_append_callback(update, context)
    elif data.startswith("cl_"):
        await client_detail_callback(update, context)
    elif data.startswith("delall_"):
        await delete_all_callback(update, context)
    elif data.startswith("reset_"):
        await reset_callback(update, context)
    elif data.startswith("cyc_"):
        await cycle_survey_callback(update, context)
    elif data.startswith("wbs_"):
        await wellbeing_survey_callback(update, context)
    elif data.startswith("admin_"):
        if data == "admin_dashboard_open":
            await update.callback_query.answer()
            await admin_dashboard(update.callback_query, context)
        else:
            await admin_dashboard_callback(update, context)
    elif data.startswith("top_full"):
        await leaderboard_full_callback(update, context)
    elif data.startswith("consult_view_"):
        await consult_view_callback(update, context)
    else:
        await update.callback_query.answer()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Ошибка: {context.error}", exc_info=context.error)


# ── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_stop(post_stop)
        .build()
    )

    app.add_handler(CommandHandler("force_import", force_import_cmd))
    app.add_handler(CommandHandler("dashboard",    admin_dashboard))
    app.add_handler(CommandHandler("start",    start_cmd))
    app.add_handler(CommandHandler("stats",    stats_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("workout",  workout_cmd))
    app.add_handler(CommandHandler("food",     food_cmd))
    app.add_handler(CommandHandler("goals",    goals_cmd))
    app.add_handler(CommandHandler("anketa",   anketa_cmd))
    app.add_handler(CommandHandler("records",  records_cmd))
    app.add_handler(CommandHandler("weighin",  weighin_cmd))
    app.add_handler(CommandHandler("top",      leaderboard_cmd))
    app.add_handler(CommandHandler("reset",    reset_cmd))
    # ── Инструменты защиты данных (только тренер) ──
    app.add_handler(CommandHandler("journal",  journal_cmd))
    app.add_handler(CommandHandler("restore",  restore_cmd))
    app.add_handler(CommandHandler("backup",   backup_cmd))
    app.add_handler(CommandHandler("health",   health_cmd))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_error_handler(error_handler)

    jq = app.job_queue
    if jq:
        # 06:00 — итоги прошлой недели (только пн)
        jq.run_daily(check_weekly_plan_completion,
                     time=time(hour=6,  minute=0,  tzinfo=TZ_FOR_JOBS))
        # 07:30 — авто-дашборд тренеру (только пн)
        jq.run_daily(send_trainer_weekly_report,
                     time=time(hour=7,  minute=30, tzinfo=TZ_FOR_JOBS))
        # 08:00 — напоминание взвеситься (только вс)
        jq.run_daily(send_weighin_reminders,
                     time=time(hour=8,  minute=0,  tzinfo=TZ_FOR_JOBS))
        # 09:00 — утренний пуш: тренировка или питание (каждый день)
        jq.run_daily(send_training_reminders,
                     time=time(hour=9,  minute=0,  tzinfo=TZ_FOR_JOBS))
        # 10:00 — ежемесячный отчёт (только 1-е число)
        jq.run_daily(send_monthly_reports_job,
                     time=time(hour=10, minute=0,  tzinfo=TZ_FOR_JOBS))
        # 12:00 — дневной дожим: профиль / пропуск / питание
        jq.run_daily(send_engagement_push,
                     time=time(hour=12, minute=0,  tzinfo=TZ_FOR_JOBS))
        # 10:00 — недельный AI-отчёт каждому (только пн)
        jq.run_daily(send_weekly_nutrition_reports,
                     time=time(hour=10, minute=0,  tzinfo=TZ_FOR_JOBS))
        # 21:25 — умный вечерний пуш по теме дня недели (каждый день)
        jq.run_daily(send_evening_push,
                     time=time(hour=21, minute=25, tzinfo=TZ_FOR_JOBS))
        # Каждые 48ч — напоминание тем кто не заполнил расписание
        jq.run_repeating(remind_schedule,  interval=172800, first=86400)
        # Каждые 45с — сохраняем состояния на диск
        jq.run_repeating(flush_states_job, interval=45,     first=45)

        # ── ЗАЩИТА ДАННЫХ ──────────────────────────────────────────────
        # 03:30 — ежедневная резервная копия всех листов
        jq.run_daily(daily_backup_job,
                     time=time(hour=3,  minute=30, tzinfo=TZ_FOR_JOBS))
        # Каждые 5 мин — добор начислений, не сохранённых из-за сбоя Sheets
        jq.run_repeating(replay_pending_ops, interval=300,  first=120)
        # Каждый час — сверка таблицы с несгораемыми марками + автопочинка
        jq.run_repeating(integrity_audit,    interval=3600, first=300)

    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

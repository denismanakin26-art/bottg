import os
import asyncio
import random
import sqlite3
import aiohttp
import matplotlib
matplotlib.use('Agg')  # Работа без GUI
import matplotlib.pyplot as plt
import numpy as np
from io import BytesIO
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile

# Настройки из переменных окружения (для безопасного деплоя)
BOT_TOKEN = os.getenv("BOT_TOKEN", "8842091636:AAFEhRb5HexnwI4mkSxQyC68Pv16Gqavj_0")
DB_PATH = os.getenv("DB_PATH", "bot_stats.db")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS stats
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  drink_yes INTEGER DEFAULT 0,
                  drink_no INTEGER DEFAULT 0,
                  hookah_yes INTEGER DEFAULT 0,
                  hookah_no INTEGER DEFAULT 0)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS achievements
                 (user_id INTEGER,
                  achievement TEXT,
                  unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (user_id, achievement))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS calendar
                 (user_id INTEGER,
                  date TEXT,
                  drink INTEGER DEFAULT 0,
                  hookah INTEGER DEFAULT 0,
                  PRIMARY KEY (user_id, date))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS reminders
                 (user_id INTEGER PRIMARY KEY,
                  chat_id INTEGER,
                  enabled INTEGER DEFAULT 0)''')
    
    conn.commit()
    conn.close()


def ensure_user(user_id: int, username: str = None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT 1 FROM stats WHERE user_id = ?', (user_id,))
    if not c.fetchone():
        c.execute('INSERT INTO stats (user_id, username) VALUES (?, ?)', (user_id, username))
        conn.commit()
    elif username:
        c.execute('UPDATE stats SET username = ? WHERE user_id = ?', (username, user_id))
        conn.commit()
    conn.close()


def update_stat(user_id: int, column: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f'UPDATE stats SET {column} = {column} + 1 WHERE user_id = ?', (user_id,))
    if c.rowcount == 0:
        c.execute(f'INSERT INTO stats (user_id, {column}) VALUES (?, 1)', (user_id,))
        conn.commit()
    conn.close()


def get_stats(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT drink_yes, drink_no, hookah_yes, hookah_no FROM stats WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result if result else (0, 0, 0, 0)


def reset_stats(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM stats WHERE user_id = ?', (user_id,))
    c.execute('DELETE FROM achievements WHERE user_id = ?', (user_id,))
    c.execute('DELETE FROM calendar WHERE user_id = ?', (user_id,))
    c.execute('DELETE FROM reminders WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()


def add_achievement(user_id: int, achievement: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('INSERT INTO achievements (user_id, achievement) VALUES (?, ?)', (user_id, achievement))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False


def get_achievements(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT achievement, unlocked_at FROM achievements WHERE user_id = ?', (user_id,))
    result = c.fetchall()
    conn.close()
    return result


def get_top_users(limit: int = 10):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT username, (drink_yes + drink_no + hookah_yes + hookah_no) as total 
                 FROM stats 
                 WHERE total > 0
                 ORDER BY total DESC 
                 LIMIT ?''', (limit,))
    result = c.fetchall()
    conn.close()
    return result


# ========== КАЛЕНДАРЬ ==========
def mark_today(user_id: int, drink: int, hookah: int):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO calendar (user_id, date, drink, hookah)
                 VALUES (?, ?, ?, ?)
                 ON CONFLICT(user_id, date)
                 DO UPDATE SET drink = ?, hookah = ?''',
              (user_id, today, drink, hookah, drink, hookah))
    conn.commit()
    conn.close()


def unmark_today(user_id: int):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM calendar WHERE user_id = ? AND date = ?', (user_id, today))
    conn.commit()
    conn.close()


def get_month_calendar(user_id: int, year: int, month: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT date, drink, hookah FROM calendar 
                 WHERE user_id = ? AND strftime('%Y-%m', date) = ?''', 
              (user_id, f"{year:04d}-{month:02d}"))
    rows = c.fetchall()
    conn.close()
    
    month_names = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                   "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
    
    text = f"📅 <b>{month_names[month]} {year}</b>\n\n"
    
    if not rows:
        text += "Нет отметок за этот месяц.\n"
    else:
        for date_str, drink, hookah in sorted(rows):
            day = date_str.split('-')[2]
            if drink and hookah:
                icon = "🎉"
            elif drink:
                icon = "🍷"
            elif hookah:
                icon = "🚬"
            else:
                icon = "❓"
            text += f"{day}.{month:02d} — {icon}\n"
    
    text += "\n🍷 = алкоголь, 🚬 = кальян, 🎉 = оба"
    return text


# ========== ГРАФИК ==========
async def generate_chart(user_id: int, year: int, month: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT date, drink, hookah FROM calendar 
                 WHERE user_id = ? AND strftime('%Y-%m', date) = ?''', 
              (user_id, f"{year:04d}-{month:02d}"))
    rows = {row[0]: (row[1], row[2]) for row in c.fetchall()}
    conn.close()
    
    days = list(range(1, 32))
    drink_vals = []
    hookah_vals = []
    
    for day in days:
        day_str = f"{year:04d}-{month:02d}-{day:02d}"
        d, h = rows.get(day_str, (0, 0))
        drink_vals.append(d)
        hookah_vals.append(h)
    
    fig, ax = plt.subplots(figsize=(14, 6))
    x = np.arange(len(days))
    width = 0.35
    
    ax.bar(x - width/2, drink_vals, width, label='Алкоголь', color='#FF6B6B', alpha=0.85, edgecolor='white')
    ax.bar(x + width/2, hookah_vals, width, label='Кальян', color='#4ECDC4', alpha=0.85, edgecolor='white')
    
    ax.set_xlabel('День месяца', fontsize=11)
    ax.set_ylabel('Отметка', fontsize=11)
    month_names = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
                   "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
    ax.set_title(f'Привычки — {month_names[month]} {year}', fontsize=13, fontweight='bold', pad=15)
    ax.set_xticks(x)
    ax.set_xticklabels(days)
    ax.legend(fontsize=10)
    ax.grid(axis='y', alpha=0.3, linestyle='--')
    ax.set_ylim(0, 1.5)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    
    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf


# ========== НАПОМИНАНИЯ ==========
def set_reminder(user_id: int, chat_id: int, enabled: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO reminders (user_id, chat_id, enabled)
                 VALUES (?, ?, ?)
                 ON CONFLICT(user_id)
                 DO UPDATE SET enabled = ?, chat_id = ?''',
              (user_id, chat_id, enabled, enabled, chat_id))
    conn.commit()
    conn.close()


def get_reminder_status(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT enabled FROM reminders WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0


async def reminder_task():
    sent_today = set()
    while True:
        now = datetime.now()
        key = f"{now.year}-{now.month}-{now.day}"
        
        if now.hour == 21 and now.minute == 0 and key not in sent_today:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT user_id, chat_id FROM reminders WHERE enabled = 1')
            users = c.fetchall()
            conn.close()
            
            for user_id, chat_id in users:
                try:
                    await bot.send_message(
                        chat_id,
                        "⏰ <b>Вечернее напоминание!</b>\n\n"
                        "Не забудь отметить в календаре, пил ли ты сегодня алкоголь или курил кальян.\n\n"
                        "📅 Открыть календарь →",
                        reply_markup=main_kb
                    )
                except Exception:
                    pass
            
            sent_today.add(key)
            await asyncio.sleep(60)
        else:
            if now.hour != 21:
                sent_today.clear()
            await asyncio.sleep(30)


# ========== ДОСТИЖЕНИЯ ==========
async def check_achievements(message: types.Message, user_id: int):
    stats = get_stats(user_id)
    dy, dn, hy, hn = stats
    total = sum(stats)
    new_achievements = []
    
    checks = [
        (dn >= 10, "🏆 Король трезвости", "Ты такой трезвый, что даже вода от тебя пьянеет!"),
        (dy >= 10, "🍺 Печеночный марафонец", "Твоя печень уже в Зале Славы!"),
        (hy >= 10, "🚬 Дымовой шейх", "У тебя больше дыма, чем у вулкана!"),
        (hn >= 10, "🫁 Чистые лёгкие", "Ты дышишь чище, чем реклама йогурта!"),
        (total >= 50, "🤔 Нерешительный", "Ты нажал кнопку 50 раз — это уже зависимость!"),
        (total >= 100, "🎲 Бог рандома", "Статистически ты должен был найти смысл жизни!"),
    ]
    
    for condition, name, desc in checks:
        if condition and add_achievement(user_id, name):
            new_achievements.append((name, desc))
    
    if new_achievements:
        text = "🎉 <b>НОВОЕ ДОСТИЖЕНИЕ!</b>\n\n"
        for name, desc in new_achievements:
            text += f"<b>{name}</b>\n<i>{desc}</i>\n\n"
        await message.answer(text)


# ========== МЕМЫ ==========
async def get_random_meme():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://meme-api.com/gimme', timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if not data.get('nsfw', False):
                        return data.get('url')
    except Exception:
        pass
    return None


async def send_meme(message: types.Message):
    meme_url = await get_random_meme()
    if meme_url:
        await message.answer_photo(meme_url, caption="😂 Лови мем!")
    else:
        await message.answer("😅 Мем не загрузился, но шутка была огонь!")


# ========== КЛАВИАТУРЫ ==========
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🍷 Пить или не пить?")],
        [KeyboardButton(text="🚬 Курить кальян или нет?")],
        [KeyboardButton(text="📊 Моя статистика"), KeyboardButton(text="🏆 Мои достижения")],
        [KeyboardButton(text="📅 Календарь"), KeyboardButton(text="📊 Топ друзей")],
        [KeyboardButton(text="⏰ Напоминание")],
    ],
    resize_keyboard=True
)

calendar_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🍷 Отметить: пил")],
        [KeyboardButton(text="🚬 Отметить: курил")],
        [KeyboardButton(text="🎉 Отметить: и то, и другое")],
        [KeyboardButton(text="❌ Удалить отметку за сегодня")],
        [KeyboardButton(text="📆 Показать календарь")],
        [KeyboardButton(text="📈 График привычек")],
        [KeyboardButton(text="🔙 Назад в меню")],
    ],
    resize_keyboard=True
)


# ========== ШУТКИ ==========
DRINK_YES = [
    "🍾 Пить! Врачи говорят, что бокал вина продлевает жизнь. А если выпить бутылку — наверное, вечность!",
    "🥂 Да! Алкоголь — это жидкое решение всех проблем. Правда, временное, но всё же решение.",
    "🍻 Однозначно пить! Сегодня пятница... или понедельник... или какая разница, какой сегодня день?",
    "🍷 Пить! Говорят, вино полезно для сердца. А у тебя сердце есть — значит, полезно!",
    "🥃 Да! Ты не пьёшь каждый день. Иногда пропускаешь утро.",
]

DRINK_NO = [
    "🚫 Не пить! Помни: сегодня ты отказываешься от бокала, а завтра от похмелья. Это инвестиция!",
    "🍵 Нет, лучше чай. Чай тоже можно пить из красивого бокала — никто не запрещает!",
    "💪 Не сегодня! Твоя печень уже отправила тебе push-уведомление: 'Дай отдохнуть, бро'.",
    "😴 Не пить. Завтра ты проснёшься свежим, бодрым и с чётким пониманием, зачем ты живёшь.",
    "🧘 Нет. Сегодня ты — храм. А в храмы обычно не наливают.",
]

HOOKAH_YES = [
    "🚬 Курить! Дым — это душа кальяна, а ты — его тело. Соединись воедино!",
    "💨 Да! Кальян — это единственный законный способ пускать дым изо рта и не быть драконом.",
    "🍇 Однозначно курить! Ты же не куришь каждый день. Только по чётным, нечётным и праздникам.",
    "🌬 Да! Сегодня ты — восточный шейх. Только без верблюда и гарема, но с кальяном.",
    "🔥 Курить! Если кальян плохо тянется — значит, жизнь тоже даётся нелегко. Но мы не сдаёмся!",
]

HOOKAH_NO = [
    "🚭 Не курить! Твои лёгкие сказали: 'Спасибо, что вспомнил о нас'.",
    "🫁 Нет. Сегодня ты дышишь чистым воздухом. Завтра тоже. Послезавтра... ну, посмотрим.",
    "🧊 Не сегодня! Лёгкие — не пепельница, а кальян — не кислородный баллон.",
    "💪 Нет! Ты сильнее дыма. Сегодня ты победитель, а не дымовая завеса.",
    "🌿 Не курить. Пойди лучше на свежий воздух — там тоже есть дым, но от костров соседей.",
]


# ========== ОБРАБОТЧИКИ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    username = message.from_user.username or message.from_user.full_name
    ensure_user(message.from_user.id, username)
    await message.answer(
        "Привет! 👋\n\nНе можешь решить — пить сегодня или покурить кальян?\n"
        "Нажми кнопку ниже, и я помогу! (Со шуткой, мемом и достижением)\n\n"
        "📅 <b>Календарь</b> — отмечай дни, когда пил/курил\n"
        "📈 <b>График</b> — визуализация привычек за месяц\n"
        "📊 <b>Топ друзей</b> — кто больше всех нажимал кнопки\n"
        "⏰ <b>Напоминание</b> — в 21:00 напомню отметить день\n\n"
        "📋 <b>Команды:</b>\n/stats — статистика\n/achievements — достижения\n/reset — сбросить всё",
        reply_markup=main_kb
    )


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    stats = get_stats(message.from_user.id)
    total = sum(stats)
    text = (
        f"📊 <b>Твоя статистика решений</b>\n\n"
        f"🍷 <b>Алкоголь:</b>\n"
        f"   Пить: {stats[0]} | Не пить: {stats[1]}\n\n"
        f"🚬 <b>Кальян:</b>\n"
        f"   Курить: {stats[2]} | Не курить: {stats[3]}\n\n"
        f"📈 Всего решений: <b>{total}</b>"
    )
    await message.answer(text)


@dp.message(Command("achievements"))
async def cmd_achievements(message: types.Message):
    ach = get_achievements(message.from_user.id)
    if not ach:
        await message.answer("🏆 У тебя пока нет достижений. Жми кнопки и зарабатывай!")
        return
    
    text = "🏆 <b>Твои достижения:</b>\n\n"
    for name, date in ach:
        text += f"✅ {name} — <i>{date}</i>\n"
    
    total = sum(get_stats(message.from_user.id))
    text += f"\n📈 Всего решений: {total}"
    await message.answer(text)


@dp.message(Command("reset"))
async def cmd_reset(message: types.Message):
    reset_stats(message.from_user.id)
    await message.answer("🗑 Статистика, достижения, календарь и напоминания сброшены!", reply_markup=main_kb)


@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    top = get_top_users()
    if not top:
        await message.answer("📊 Пока никто не играл. Будь первым!")
        return
    
    text = "🏆 <b>Топ друзей</b>\n\n"
    for i, (username, total) in enumerate(top, 1):
        name = username or f"Аноним"
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, "▫️")
        text += f"{medal} {i}. {name} — <b>{total}</b> решений\n"
    
    await message.answer(text)


@dp.message(lambda msg: msg.text == "📊 Моя статистика")
async def btn_stats(message: types.Message):
    await cmd_stats(message)


@dp.message(lambda msg: msg.text == "🏆 Мои достижения")
async def btn_achievements(message: types.Message):
    await cmd_achievements(message)


@dp.message(lambda msg: msg.text == "📊 Топ друзей")
async def btn_top(message: types.Message):
    await cmd_top(message)


# ========== НАПОМИНАНИЯ ==========
@dp.message(lambda msg: msg.text == "⏰ Напоминание")
async def btn_reminder(message: types.Message):
    status = get_reminder_status(message.from_user.id)
    if status:
        text = ("⏰ <b>Напоминание включено</b>\n\n"
                "Каждый день в <b>21:00</b> я буду присылать напоминание "
                "отметить день в календаре.\n\n"
                "Нажми кнопку ниже, чтобы выключить.")
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🔕 Выключить напоминание")], [KeyboardButton(text="🔙 Назад в меню")]],
            resize_keyboard=True
        )
    else:
        text = ("🔕 <b>Напоминание выключено</b>\n\n"
                "Я могу каждый день в <b>21:00</b> напоминать тебе "
                "отметить день в календаре.\n\n"
                "Нажми кнопку ниже, чтобы включить.")
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="🔔 Включить напоминание")], [KeyboardButton(text="🔙 Назад в меню")]],
            resize_keyboard=True
        )
    await message.answer(text, reply_markup=kb)


@dp.message(lambda msg: msg.text == "🔔 Включить напоминание")
async def enable_reminder(message: types.Message):
    set_reminder(message.from_user.id, message.chat.id, 1)
    await message.answer(
        "🔔 <b>Напоминание включено!</b>\n\n"
        "Теперь каждый день в 21:00 я буду писать тебе.\n\n"
        "Не забудь оставить бота запущенным!",
        reply_markup=main_kb
    )


@dp.message(lambda msg: msg.text == "🔕 Выключить напоминание")
async def disable_reminder(message: types.Message):
    set_reminder(message.from_user.id, message.chat.id, 0)
    await message.answer("🔕 <b>Напоминание выключено.</b>", reply_markup=main_kb)


# ========== КАЛЕНДАРЬ ==========
@dp.message(lambda msg: msg.text == "📅 Календарь")
async def btn_calendar_menu(message: types.Message):
    await message.answer(
        "📅 <b>Календарь привычек</b>\n\n"
        "Отмечай дни, когда пил алкоголь или курил кальян.\n"
        "Смотри график за месяц и следи за динамикой!",
        reply_markup=calendar_kb
    )


@dp.message(lambda msg: msg.text == "🔙 Назад в меню")
async def back_to_main(message: types.Message):
    await message.answer("Главное меню:", reply_markup=main_kb)


@dp.message(lambda msg: msg.text == "🍷 Отметить: пил")
async def mark_drink(message: types.Message):
    ensure_user(message.from_user.id, message.from_user.username)
    mark_today(message.from_user.id, drink=1, hookah=0)
    await message.answer("✅ Отмечено: сегодня пил алкоголь! 🍷")


@dp.message(lambda msg: msg.text == "🚬 Отметить: курил")
async def mark_hookah(message: types.Message):
    ensure_user(message.from_user.id, message.from_user.username)
    mark_today(message.from_user.id, drink=0, hookah=1)
    await message.answer("✅ Отметено: сегодня курил кальян! 🚬")


@dp.message(lambda msg: msg.text == "🎉 Отметить: и то, и другое")
async def mark_both(message: types.Message):
    ensure_user(message.from_user.id, message.from_user.username)
    mark_today(message.from_user.id, drink=1, hookah=1)
    await message.answer("✅ Отмечено: сегодня и пил, и курил! 🎉")


@dp.message(lambda msg: msg.text == "❌ Удалить отметку за сегодня")
async def unmark_today_cmd(message: types.Message):
    unmark_today(message.from_user.id)
    await message.answer("🗑 Отметка за сегодня удалена.")


@dp.message(lambda msg: msg.text == "📆 Показать календарь")
async def show_calendar(message: types.Message):
    now = datetime.now()
    cal_text = get_month_calendar(message.from_user.id, now.year, now.month)
    await message.answer(cal_text)


@dp.message(lambda msg: msg.text == "📈 График привычек")
async def show_chart(message: types.Message):
    now = datetime.now()
    buf = await generate_chart(message.from_user.id, now.year, now.month)
    photo = BufferedInputFile(buf.read(), filename="habit_chart.png")
    await message.answer_photo(
        photo,
        caption=f"📈 График привычек за {now.month:02d}.{now.year}"
    )


# ========== РЕШЕНИЯ ==========
@dp.message(lambda msg: msg.text == "🍷 Пить или не пить?")
async def drink_decision(message: types.Message):
    ensure_user(message.from_user.id, message.from_user.username)
    decision = random.choice([True, False])
    column = 'drink_yes' if decision else 'drink_no'
    update_stat(message.from_user.id, column)
    
    answer = random.choice(DRINK_YES if decision else DRINK_NO)
    await message.answer(answer)
    await send_meme(message)
    await check_achievements(message, message.from_user.id)


@dp.message(lambda msg: msg.text == "🚬 Курить кальян или нет?")
async def hookah_decision(message: types.Message):
    ensure_user(message.from_user.id, message.from_user.username)
    decision = random.choice([True, False])
    column = 'hookah_yes' if decision else 'hookah_no'
    update_stat(message.from_user.id, column)
    
    answer = random.choice(HOOKAH_YES if decision else HOOKAH_NO)
    await message.answer(answer)
    await send_meme(message)
    await check_achievements(message, message.from_user.id)


# ========== ЗАПУСК ==========
async def main():
    init_db()
    print("Бот запущен! 🚀")
    await asyncio.gather(
        dp.start_polling(bot),
        reminder_task()
    )


if __name__ == "__main__":
    asyncio.run(main())

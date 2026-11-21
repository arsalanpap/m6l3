import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
import telebot
from config import TOKEN

bot = telebot.TeleBot(TOKEN)
DB_NAME = "reminders.db"
TZ_UTC8 = timezone(timedelta(hours=8))

bot = telebot.TeleBot(TOKEN)
user_data = {}  # Временное хранилище текста напоминания для каждого пользователя

def now_utc8() -> datetime:
    return datetime.now(TZ_UTC8)

# Создание таблицы при старте
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                is_sent INTEGER DEFAULT 0
            )
        ''')
        conn.commit()

# Хендлер команды /start
@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.send_message(
        message.chat.id,
        "👋 Привет! Я бот-напоминалка (работаю по часовому поясу UTC+8).\n\n"
        "Доступные команды:\n"
        "/remind — создать новое напоминание\n"
        "/list — посмотреть активные напоминания"
    )

# Шаг 1: Команда /remind — запрашиваем текст
@bot.message_handler(commands=['remind'])
def cmd_remind(message):
    msg = bot.send_message(message.chat.id, "О чём вам напомнить?")
    bot.register_next_step_handler(msg, process_text)

# Шаг 2: Получаем текст и запрашиваем дату/время
def process_text(message):
    if not message.text or message.text.startswith('/'):
        bot.send_message(message.chat.id, "Создание напоминания отменено.")
        return

    user_data[message.from_user.id] = message.text
    current_str = now_utc8().strftime("%d.%m.%Y %H:%M")
    msg = bot.send_message(
        message.chat.id,
        f"Когда напомнить (по времени UTC+8)?\n"
        f"Сейчас: {current_str}\n\n"
        f"Введите дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ"
    )
    bot.register_next_step_handler(msg, process_time)

# Шаг 3: Проверяем время и записываем в SQLite
def process_time(message):
    if not message.text or message.text.startswith('/'):
        bot.send_message(message.chat.id, "Создание напоминания отменено.")
        return

    try:
        naive_dt = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
        dt_utc8 = naive_dt.replace(tzinfo=TZ_UTC8)

        if dt_utc8 <= now_utc8():
            msg = bot.send_message(
                message.chat.id,
                "Это время уже прошло (по UTC+8). Введите дату и время в будущем:"
            )
            bot.register_next_step_handler(msg, process_time)
            return

        iso_time = dt_utc8.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        msg = bot.send_message(
            message.chat.id,
            "Неверный формат. Введите так: ДД.ММ.ГГГГ ЧЧ:ММ (например, 21.11.2026 14:00)"
        )
        bot.register_next_step_handler(msg, process_time)
        return

    text = user_data.pop(message.from_user.id, "Без текста")

    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            "INSERT INTO reminders (user_id, text, remind_at) VALUES (?, ?, ?)",
            (message.from_user.id, text, iso_time)
        )
        conn.commit()

    bot.send_message(
        message.chat.id,
        f"⏰ Напоминание установлено на {dt_utc8.strftime('%d.%m.%Y %H:%M')} (UTC+8):\n«{text}»"
    )

# Просмотр активных напоминаний
@bot.message_handler(commands=['list'])
def cmd_list(message):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute(
            "SELECT text, remind_at FROM reminders WHERE user_id = ? AND is_sent = 0 ORDER BY remind_at ASC",
            (message.from_user.id,)
        )
        rows = cursor.fetchall()

    if not rows:
        bot.send_message(message.chat.id, "У вас нет активных напоминаний.")
        return

    lines = ["⏰ **Запланированные напоминания (UTC+8):**\n"]
    for text, remind_at_str in rows:
        dt = datetime.strptime(remind_at_str, "%Y-%m-%d %H:%M:%S")
        lines.append(f"• {dt.strftime('%d.%m.%Y %H:%M')} — {text}")

    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="Markdown")

# Фоновый поток для проверки и отправки напоминаний
def scheduler():
    while True:
        now_iso = now_utc8().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with sqlite3.connect(DB_NAME) as conn:
                cursor = conn.execute(
                    "SELECT id, user_id, text FROM reminders WHERE is_sent = 0 AND remind_at <= ?",
                    (now_iso,)
                )
                due_reminders = cursor.fetchall()

                for rem_id, user_id, text in due_reminders:
                    try:
                        bot.send_message(user_id, f"🔔 **Напоминание!**\n\n{text}", parse_mode="Markdown")
                        conn.execute("UPDATE reminders SET is_sent = 1 WHERE id = ?", (rem_id,))
                    except Exception as e:
                        print(f"Ошибка отправки пользователю {user_id}: {e}")

                conn.commit()
        except Exception as e:
            print(f"Ошибка БД в планировщике: {e}")

        time.sleep(15)

if __name__ == "__main__":
    init_db()
    # Запускаем проверку напоминаний в отдельном фоновом потоке
    threading.Thread(target=scheduler, daemon=True).start()
    print("Бот запущен...")
    bot.infinity_polling()
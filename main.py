# --- imports.py ---
import json
import time
import sqlite3
import feedparser
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler
)

# --- help.py ---
HELP_TEXT = """
Доступные команды:

/help
Показать это сообщение

/queue
Показать очередь публикаций

/queue_get <guid>
Показать элемент очереди

/queue_del <guid>
Удалить элемент из очереди

/queue_delay <guid> <минуты>
Сдвинуть время публикации

/delay <минуты>
Установить задержку между постами
""".strip()


from config import BOT_TOKEN, CHANNEL_ID, RSS_URL, CHECK_INTERVAL, DB_PATH, ADMIN_IDS, DEFAULT_DELAY_MINUTES


# --- db/core.py ---
def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = db()
    c = conn.cursor()

    c.execute("CREATE TABLE IF NOT EXISTS posted (guid TEXT PRIMARY KEY)")
    c.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            guid TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            publish_at INTEGER NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    c.execute("""
        INSERT OR IGNORE INTO settings (key, value)
        VALUES ('delay_minutes', ?)
    """, (str(DEFAULT_DELAY_MINUTES),))

    conn.commit()
    conn.close()


# --- db/settings.py ---
def get_delay_minutes() -> int:
    conn = db()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='delay_minutes'")
    v = int(c.fetchone()[0])
    conn.close()
    return v


def set_delay_minutes(v: int):
    conn = db()
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO settings VALUES ('delay_minutes', ?)",
        (str(v),)
    )
    conn.commit()
    conn.close()


# --- db/posted.py ---
def is_posted(guid: str) -> bool:
    conn = db()
    c = conn.cursor()
    c.execute("SELECT 1 FROM posted WHERE guid=?", (guid,))
    r = c.fetchone()
    conn.close()
    return r is not None


def mark_posted(guid: str):
    conn = db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO posted VALUES (?)", (guid,))
    conn.commit()
    conn.close()


# --- db/queue.py ---
def add_to_queue(guid: str, payload: dict, publish_at: int):
    conn = db()
    c = conn.cursor()
    c.execute("""
        INSERT OR IGNORE INTO queue VALUES (?, ?, ?)
    """, (guid, json.dumps(payload, ensure_ascii=False), publish_at))
    conn.commit()
    conn.close()


def get_last_publish_time():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT MAX(publish_at) FROM queue")
    r = c.fetchone()[0]
    conn.close()
    return r


def get_all_queue():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT guid, payload, publish_at FROM queue ORDER BY publish_at")
    rows = c.fetchall()
    conn.close()
    return rows


def get_queue_item(guid: str):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT guid, payload, publish_at FROM queue WHERE guid=?", (guid,))
    row = c.fetchone()
    conn.close()
    return row


def remove_from_queue(guid: str):
    conn = db()
    c = conn.cursor()
    c.execute("DELETE FROM queue WHERE guid=?", (guid,))
    conn.commit()
    conn.close()


def update_publish_time(guid: str, publish_at: int):
    conn = db()
    c = conn.cursor()
    c.execute(
        "UPDATE queue SET publish_at=? WHERE guid=?",
        (publish_at, guid)
    )
    conn.commit()
    conn.close()


# --- rss/parser.py ---
def extract(entry) -> dict:
    return {
        "id": entry.get("guid") or entry.get("id"),
        "title": entry.get("title"),
        "author": entry.get("dc_creator") or entry.get("author"),
        "published": entry.get("published"),
        "link": entry.get("link"),
        "content": (
            entry.content[0].value.strip()
            if "content" in entry and entry.content
            else ""
        )
    }


# --- rss/job.py ---
async def rss_job(context: ContextTypes.DEFAULT_TYPE):
    feed = feedparser.parse(RSS_URL)

    for entry in feed.entries:
        guid = entry.get("guid") or entry.get("id")
        if not guid or is_posted(guid):
            continue

        payload = extract(entry)
        delay = get_delay_minutes() * 60

        last = get_last_publish_time()
        publish_at = (last + delay) if last else int(time.time()) + delay

        add_to_queue(guid, payload, publish_at)
        mark_posted(guid)

        context.job_queue.run_once(
            publish_job,
            when=publish_at - int(time.time()),
            data={"guid": guid}
        )


# --- publish/job.py ---
async def publish_job(context: ContextTypes.DEFAULT_TYPE):
    guid = context.job.data["guid"]

    conn = db()
    c = conn.cursor()
    c.execute(
        "SELECT payload FROM queue WHERE guid=?", (guid,)
    )
    row = c.fetchone()
    conn.close()

    if not row:
        return

    payload = json.loads(row[0])
    title = payload["title"]
    if title.count("'")>=2:
        title = title[title.index("'")+1:title.rindex("'")]
    await context.bot.send_message(
        chat_id=CHANNEL_ID,
        text=f"""{title}\n\n{payload["content"]}\n\n{payload["link"]}""",
        disable_web_page_preview=True
    )

    remove_from_queue(guid)


# --- admin/auth.py ---
def is_admin(update: Update) -> bool:
    # print(update.effective_user.username)
    return update.effective_user.username in ADMIN_IDS


# --- admin/queue_commands.py ---
async def queue_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    rows = get_all_queue()
    if not rows:
        await update.message.reply_text("Очередь пуста")
        return

    lines = []
    for guid, payload, publish_at in rows:
        title = json.loads(payload).get("title")
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(publish_at))
        lines.append(f"{guid}\n{title}\n🕒 {ts}\n")

    await update.message.reply_text("\n".join(lines))


async def queue_get(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Использование: /queue_get <guid>")
        return

    row = get_queue_item(context.args[0])
    if not row:
        await update.message.reply_text("Не найдено")
        return

    guid, payload, publish_at = row
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(publish_at))

    await update.message.reply_text(
        f"GUID: {guid}\n🕒 {ts}\n\n{payload}"
    )


async def queue_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if not context.args:
        await update.message.reply_text("Использование: /queue_del <guid>")
        return

    remove_from_queue(context.args[0])
    await update.message.reply_text("Удалено")


async def queue_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if len(context.args) != 2 or not context.args[1].isdigit():
        await update.message.reply_text(
            "Использование: /queue_delay <guid> <минуты>"
        )
        return

    guid = context.args[0]
    minutes = int(context.args[1])

    row = get_queue_item(guid)
    if not row:
        await update.message.reply_text("Не найдено")
        return

    new_time = int(time.time()) + minutes * 60
    update_publish_time(guid, new_time)

    await update.message.reply_text(
        f"Новое время публикации через {minutes} минут"
    )


# --- admin/settings_commands.py ---
async def delay_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /delay <минуты>")
        return

    v = int(context.args[0])
    set_delay_minutes(v)
    await update.message.reply_text(f"Задержка установлена: {v} минут")


# --- jobs/restore.py ---
def restore_jobs(app):
    now = int(time.time())

    for guid, payload, publish_at in get_all_queue():
        delay = max(0, publish_at - now)
        app.job_queue.run_once(
            publish_job,
            when=delay,
            data={"guid": guid}
        )


# --- main.py ---
def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("delay", delay_cmd))
    app.add_handler(CommandHandler("queue", queue_list))
    app.add_handler(CommandHandler("queue_get", queue_get))
    app.add_handler(CommandHandler("queue_del", queue_del))
    app.add_handler(CommandHandler("queue_delay", queue_delay))
    app.add_handler(
        CommandHandler(
            "help",
            lambda update, context: update.message.reply_text(HELP_TEXT)
        )
    )
    app.add_handler(
        CommandHandler(
            "start",
            lambda update, context: update.message.reply_text(HELP_TEXT)
        )
    )



    restore_jobs(app)

    app.job_queue.run_repeating(
        rss_job,
        interval=CHECK_INTERVAL,
        first=5
    )

    print("RSS JobQueue bot started")
    app.run_polling()


if __name__ == "__main__":
    main()

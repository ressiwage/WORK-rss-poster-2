# --- config.py ---
# токен тг бота
BOT_TOKEN = "your:bot_token"
# ид канала с собакой
CHANNEL_ID = "@your_channel-id"
# урл рсс ленты
RSS_URL = "https://autoguruclub.ru/featured/index.rss"
# интервал проверки рсс ленты в секундах (меньше интервал -- больше нагрузки на цпу)
CHECK_INTERVAL = 30
# путь к базе данных в которой хранится очередь публикаций и посты
DB_PATH = "posts.db"
# идентификаторы админов без собаки
ADMIN_IDS = {'your_username_WITHOUT_@', 'username_of_a_person_that_is_also_admin_WITHOUT_@'}
# дефолтная задержка публикации в минутах
DEFAULT_DELAY_MINUTES = 60
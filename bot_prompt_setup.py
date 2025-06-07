import os
import re
import json
from datetime import datetime
from pathlib import Path


from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

from pytz import timezone
from apscheduler.schedulers.background import BackgroundScheduler

#from assistant_core import run_assistant_via_openai
from assistant_core import generate_task_prompt_and_query, web_search, format_result_via_gpt


from openai import OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# Загрузка переменных
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")


# Клавиатура
reply_keyboard = [
    ["📝 New Task", "📋 Task List"],
    ["❌ Delete Task"]
]
markup = ReplyKeyboardMarkup(reply_keyboard, resize_keyboard=True)

# Приветственное сообщение
WELCOME_TEXT = (
    "👋 Hello! I'm Mila — your AI-agent.\n\n"
    "Select what do you want to do:\n\n"
    "📝 New task\n"
    "📋 Task list\n"
    "❌ Delete task\n\n"
    "Your task will be properly scheduled and run"
)

def start(update: Update, context: CallbackContext):
    update.message.reply_text(WELCOME_TEXT, reply_markup=markup)

# Генерация промпта
def generate_prompt(user_request: str) -> str:
    system_message = (
        "You are a prompt engineer. The user is asking for an automated news search results summary. "
        "Your task is to convert their request into a clear and stable prompt for ChatGPT to use daily or weekly. "
        "Include their preferences on frequency, time, language, format, topic, and timeframe. "
        "Do not include today's date — it will be inserted automatically later."
    )

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_request}
        ]
    )

    return response.choices[0].message.content

# Загрузка переменных окружения 
load_dotenv(override=True)
LENA_ID = int(os.getenv("LENA_ID"))
ANDY_ID = int(os.getenv("ANDY_ID"))
USER_MAP = {
    LENA_ID: "Lena",
    ANDY_ID: "Andy"
}

# Безопасное имя файла
def safe_filename(text: str) -> str:
    text = re.sub(r'\W+', '_', text.lower())
    return text[:40]

def generate_job_id(user_text: str) -> str:
    base = safe_filename(user_text)[:20] or "task"
    return base  # Больше НИЧЕГО не делаем здесь


# Сохранение промпта и добавление планировщика

from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone

scheduler = BackgroundScheduler()
scheduler.start()

def save_task_files(task_id, task_prompt, search_query, schedule, chat_id):
    Path("prompts").mkdir(exist_ok=True)

    # Сохраняем .txt
    prompt_path = Path("prompts") / f"{task_id}.txt"
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(task_prompt)

    # Сохраняем .json
    meta = {
        "task_id": task_id,
        "prompt_file": f"{task_id}.txt",
        "search_query": search_query,
        "schedule": schedule,
        "telegram_chat_id": chat_id
    }
    meta_path = Path("prompts") / f"{task_id}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    #Планировщик
    task_type = schedule.get("type")
    tz = timezone(schedule.get("timezone", "UTC"))

    if task_type == "daily":
        scheduler.add_job(
            func=run_task,
            id=task_id,
            trigger='cron',
            hour=schedule.get("hour"),
            minute=schedule.get("minute"),
            timezone=tz,
            args=[task_id]
        )
    elif task_type == "weekly":
        day_mapping = {
            "monday": "mon",
            "tuesday": "tue",
            "wednesday": "wed",
            "thursday": "thu",
            "friday": "fri",
            "saturday": "sat",
            "sunday": "sun"
        }
        day_of_week_long = schedule.get("day_of_week")
        if not day_of_week_long:
            raise ValueError("❌ Not set day_of_week for weekly task")
        day_of_week_short = day_mapping.get(day_of_week_long.lower())
        if not day_of_week_short:
            raise ValueError(f"❌ Wrong day of week: {day_of_week_long}")

        scheduler.add_job(
            func=run_task,
            id=task_id,
            trigger='cron',
            day_of_week=day_of_week_short,
            hour=schedule.get("hour"),
            minute=schedule.get("minute"),
            timezone=tz,
            args=[task_id]
        )
    elif task_type == "monthly":
        scheduler.add_job(
            func=run_task,
            id=task_id,
            trigger='cron',
            day=schedule.get("day"),
            hour=schedule.get("hour"),
            minute=schedule.get("minute"),
            timezone=tz,
            args=[task_id]
        )
    elif task_type == "once":
        from apscheduler.triggers.date import DateTrigger
        scheduler.add_job(
            func=run_task,
            id=task_id,
            trigger=DateTrigger(
                run_date=schedule.get("datetime"),
                timezone=tz
            ),
            args=[task_id]
        )
    else:
        print(f"❌ Unknowing task type: {task_type}")

        print(f"✅ Task {task_id} added with type {task_type}")




# Запуск задачи по времени
def run_task(job_id: str):
    try:
        #print(f"[{datetime.now()}] ▶ Task: {job_id}")

        # Загружаем параметры задачи
        with open(f"prompts/{job_id}.json", "r", encoding="utf-8") as f:
            meta = json.load(f)

        chat_id = meta.get("telegram_chat_id")
        search_query = meta.get("search_query")

        if not chat_id:
            print("❌ Not set telegram_chat_id")
            return

        bot = Bot(token=os.getenv("TELEGRAM_TOKEN"))

        # Если search_query нет — это напоминание
        if not search_query:
            with open(f"prompts/{job_id}.txt", "r", encoding="utf-8") as f:
                task_prompt = f.read()

            bot.send_message(chat_id=chat_id, text=f"🔔 Reminder:\n\n{task_prompt}")
            #print(f"[{datetime.now()}]  Reminder sent")
            return

        # Иначе поиск и ответ
        raw_search_result = web_search(search_query)

        if not raw_search_result.strip() or "No results" in raw_search_result:
            bot.send_message(chat_id=chat_id, text="❌ There are no search results.")
            return

        system_instructions = (
            "You are an assistant helping format web search results for the user.\n"
            "Below is the raw search result.\n\n"
            "1. Greet the user.\n"
            "2. Say: 'Here are the results for your query:\n"
            "3. Format the search results as a list.\n"
            "4. Highlight the links.\n"
            "5. If no results found, say so politely.\n"
        )

        messages = [
            {"role": "system", "content": system_instructions},
            {"role": "user", "content": raw_search_result}
        ]

        response = client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            temperature=0.3
        )

        result = response.choices[0].message.content


       # Добавляем job_id как заголовок
        header = f"📚 Results for: `{job_id}`\n\n"
        full_message = header + result

        bot.send_message(chat_id=chat_id, text=full_message[:4000], parse_mode="Markdown")


    except Exception as e:
        #print(f" Error in run_task({job_id}): {e}")
        pass


# Обработка сообщений
allowed_users = os.getenv("ALLOWED_USERS")
# Убираем пробелы и превращаем в множество int
ALLOWED_USERS = set(map(int, map(str.strip, allowed_users.split(","))))

def handle_message(update: Update, context: CallbackContext):
    
    user_id = update.effective_user.id
    #print(f"User ID: {user_id}")

    if user_id not in ALLOWED_USERS:
        update.message.reply_text("🚫 You don't have access to Mila AI Agent")
        return

    user_text = update.message.text.strip()

    # Кнопки отключают режим новой задачи
    if user_text in ["📝 New Task", "📋 Task List", "❌ Delete Task"]:
        context.user_data["new_task_mode"] = False

    # Удаление задачи
    if context.user_data.get("cancel_mode"):
        user_input = user_text.strip()

        # Определяем путь: если пользователь ввёл только task_id — дополняем .txt
        if not user_input.endswith(".txt"):
            user_input = f"{user_input}.txt"

        file_path = Path("prompts") / user_input
        json_path = file_path.with_suffix(".json")

        deleted = False

        if file_path.exists():
            file_path.unlink()
            deleted = True

        if json_path.exists():
            json_path.unlink()
            deleted = True

        if deleted:
            update.message.reply_text(f"✅ Task `{user_input}` deleted.")
        else:
            update.message.reply_text(f"❌ Task `{user_input}` is not found.")

        context.user_data["cancel_mode"] = False
        return

    # Кнопка: новая задача
    if user_text == "📝 New Task":
        context.user_data["new_task_mode"] = True
        update.message.reply_text(
            "Please answer 6 questions about the new task:\n"
	        "1.	WHAT?\n"
            "For example: I want to search for job openings with the titles ‘AI Tester’.\n"
	        "2.	WHERE?\n"
            "For example: on dice.com.\n"
	        "3.	WHEN?\n"
            "For example: every day at 8-30 am EST.\n"
	        "4.	HOW?\n"
            "For example: format the results as a list of job postings with links and translate the job descriptions into Russian.\n"
	        "5.	FOR WHICH PERIOD?\n"
            "For example: for the past week or 24 hours.\n"
	        "6.	WHERE TO SEND?\n"
            "For example: send the job list to my Telegram channel (please provide the links to your TG channel)."
        )
        return

    
    # Кнопка: список задач
    if user_text == "📋 Task List":
        user_id = update.effective_user.id
        user_name = USER_MAP.get(user_id, f"user_{user_id}")  # Получаем имя пользователя

        prompts = list(Path("prompts").glob(f"{user_name}_*.txt"))

        if not prompts:
            update.message.reply_text("❌ There are no saved tasks yet.")
        else:
            prompt_list = "\n".join([f"- {p.name}" for p in prompts])
            update.message.reply_text(f"📝 Your Task List:\n\n{prompt_list}")
        return

    # Кнопка: удалить задачу
    if user_text == "❌ Delete Task":
        user_id = update.effective_user.id
        user_name = USER_MAP.get(user_id, f"user_{user_id}")  # Получаем имя пользователя

        prompts = list(Path("prompts").glob(f"{user_name}_*.txt"))
        
        if not prompts:
            update.message.reply_text("❌ There are no task for deletion.")
            return
        prompt_list = "\n".join([f"- {p.name}" for p in prompts])
        update.message.reply_text(
            "Task List:\n\n"
            f"{prompt_list}\n\n"
            "Type file name for deletion (including `.txt`)."
        )
        context.user_data["cancel_mode"] = True
        return

    # Описание новой задачи
    if context.user_data.get("new_task_mode"):
       context.user_data["new_task_mode"] = False

    if len(user_text) < 30:
        update.message.reply_text("❌ Description is too short. Please clarify the task.")
        return

    with open("user_request_log.txt", "a", encoding="utf-8") as f:
        f.write(user_text + "\n")

    update.message.reply_text("📝 Creating prompt and search query...")

    try:
        task_prompt, search_query, schedule = generate_task_prompt_and_query(user_text)

        user_id = update.effective_user.id
        user_name = USER_MAP.get(user_id, f"user_{user_id}")

        base = generate_job_id(user_text)  # Только база из текста задачи
        task_id = f"{user_name}_{base}"    # Формируем первоначальный task_id

        original_task_id = task_id         # Сохраняем оригинальный task_id
        i = 1
        while Path(f"prompts/{task_id}.txt").exists() or Path(f"prompts/{task_id}.json").exists():
            task_id = f"{original_task_id}_{i}"
            i += 1

        chat_id = update.effective_chat.id

        save_task_files(task_id, task_prompt, search_query, schedule, chat_id)

        # Формируем красивое отображение времени
        schedule_display = ""
        if schedule.get('type') == 'once':
            # Берем дату целиком, как есть
            schedule_display = f"{schedule.get('datetime')} {schedule.get('timezone')}"
        else:
            # Для daily, weekly, monthly — берем часы и минуты
            schedule_display = f"{schedule.get('hour')}:{schedule.get('minute')} {schedule.get('timezone')} {schedule.get('type')}"

        # Отправляем сообщение пользователю
        update.message.reply_text(
            f"✅ Task `{task_id}` registered.\n\n"
            f"🔎 Search query:\n`{search_query}`\n\n"
            f"⏰ When to run the task: {schedule_display}\n\n"
            f"📋 Prompt:\n{task_prompt}"
        )

    except Exception as e:
        update.message.reply_text("❌ Oh, some error in task registration.")
        #print(f"Error: {e}")
        pass
    
    return

# Основной запуск
def main():
    updater = Updater(TELEGRAM_TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
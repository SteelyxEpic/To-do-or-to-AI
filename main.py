"""Telegram to-do bot that uses an LLM (via Groq) to turn free text into tasks.

Tasks are stored per user in data/<chat_id>.json as a tree:
    todolist -> category -> subcategory -> ... -> {task title: task entry}
A task entry is any dict containing the key "Todo".
"""

import datetime
import json
import os
from datetime import date

from dotenv import load_dotenv
from groq import Groq
from pydantic import BaseModel
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

DATA_DIR = "data"
LLM_MODEL = "openai/gpt-oss-20b"
EXPIRY_CHECK_INTERVAL_SECONDS = 600  # every 10 minutes

ADD_TASK_STATE = 1  # ConversationHandler state while waiting for task text

client = Groq(api_key=GROQ_API_KEY)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def load_user(user_id):
    """Load a user's data, or return an empty structure for new users."""
    path = f"{DATA_DIR}/{user_id}.json"
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)

    return {"todolist": {}, "all_keys": [], "expired": {}, "done": {}}


def save_user(user_id, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(f"{DATA_DIR}/{user_id}.json", "w") as f:
        json.dump(data, f, indent=4)


def get_all_user_ids():
    if not os.path.isdir(DATA_DIR):
        return []
    return [int(name[:-5]) for name in os.listdir(DATA_DIR) if name.endswith(".json")]


# ---------------------------------------------------------------------------
# Task tree helpers
# ---------------------------------------------------------------------------

class Structure(BaseModel):
    """Task fields as returned by the LLM."""
    Todo: str
    Title: str
    DueDate: str
    Time: str
    Sort: str
    done: bool = False


def get_node(tree, path):
    """Walk 'a/b/c' down the tree. Missing parts yield an empty dict."""
    node = tree
    for part in path.split("/"):
        node = node.get(part, {})
    return node


def get_or_create_node(tree, path):
    """Like get_node, but creates any missing categories along the way."""
    node = tree
    for part in path.split("/"):
        node = node.setdefault(part, {})
    return node


def split_path(path):
    """'a/b/title' -> ('a/b', 'title'); 'title' -> ('', 'title')."""
    parent, _, title = path.rpartition("/")
    return parent, title


def pop_task(tree, path):
    """Remove and return the task at `path` (or None if it isn't there)."""
    parent_path, title = split_path(path)
    parent = get_node(tree, parent_path) if parent_path else tree
    return parent.pop(title, None)


def find_tasks(tree, location=""):
    """Return [(path, task_dict), ...] for every task in the tree."""
    tasks = []
    for key, value in tree.items():
        if not isinstance(value, dict):
            continue
        path = f"{location}/{key}" if location else key
        if "Todo" in value:
            tasks.append((path, value))
        else:
            tasks.extend(find_tasks(value, path))
    return tasks


def hours_remaining(task):
    """Hours until the task is due (infinity if the due date is TBD)."""
    if task.get("DueDate", "") == "TBD":
        return float("inf")

    due = datetime.datetime.strptime(
        task.get("DueDate", "") + task.get("Time", ""), "%Y-%m-%d%H:%M"
    )
    return (due - datetime.datetime.now()).total_seconds() / 3600


def format_task(title, task):
    return (
        f"Selected task:\n{title}: {task['Todo']}\n"
        f"Due Date: {task['DueDate']} Time: {task['Time']}\n"
        f"Hours remaining: {hours_remaining(task)}"
    )


# ---------------------------------------------------------------------------
# LLM: turn free text into a task
# ---------------------------------------------------------------------------

def build_system_prompt(known_categories):
    return (
        f"You are a todo list sorter. Today's date is {date.today()} and current time is "
        f"{datetime.datetime.now().strftime('%H:%M')}."
        " Extract the task details from the user's text and output a VALID JSON object "
        "matching this schema exactly:\n"
        '{"Todo": "string", "Title": "string", "DueDate": "YYYY-MM-DD", '
        '"Time": "HH:MM", "Sort": "category/subcategory", "done": "boolean"}\n'
        "The 'Sort' field should be a string that represents the category and subcategory "
        "of the task, separated by a forward slash. If there is no subcategory, just provide "
        "the category. It can also have multiple subcategories, separated by forward slashes. "
        f"Currently, the categories are: {known_categories}. "
        "You may create new categories but only if you think it's necessary.\n"
        "If you think the description does not have enough information to determine the time, "
        "assume it as '00:00'. If you think the description does not give a due date, "
        "please write 'TBD'."
        " Do not return any pleasantries, introduction, or conversational filler. "
        "Return ONLY the raw JSON object."
    )


def extract_task(text, known_categories):
    """Ask the LLM to parse `text` into a Structure."""
    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": build_system_prompt(known_categories)},
            {"role": "user", "content": f"Extract task details from: '{text}'"},
        ],
        response_format={"type": "json_object"},  # forces raw JSON output
    )
    return Structure(**json.loads(response.choices[0].message.content))


def add_task(text, user_id):
    """Parse `text`, store the task in the user's todo list, return the Structure."""
    user = load_user(user_id)
    task_data = extract_task(text, user["all_keys"])

    if task_data.Sort not in user["all_keys"]:
        user["all_keys"].append(task_data.Sort)

    category = get_or_create_node(user["todolist"], task_data.Sort)
    category[task_data.Title] = {
        "Todo": task_data.Todo,
        "DueDate": task_data.DueDate,
        "Time": task_data.Time,
        "done": False,
        "expired": False,
    }

    save_user(user_id, user)
    return task_data


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def navigation_buttons():
    """The 'Return' and 'Get upcoming task' rows shown on most menus."""
    return [
        [InlineKeyboardButton("Return", callback_data="return")],
        [InlineKeyboardButton("Get upcoming task", callback_data="get_task_latest")],
    ]


def category_buttons(path, user_id):
    """Buttons for the categories/tasks directly under `path` ('' = top level)."""
    todolist = load_user(user_id)["todolist"]
    node = get_node(todolist, path) if path else todolist

    buttons = []
    for name, child in node.items():
        if child.get("done") or child.get("expired"):
            continue
        child_path = f"{path}/{name}" if path else name
        buttons.append([InlineKeyboardButton(name, callback_data=f"get_task_{child_path}")])
    return buttons


def main_menu(user_id):
    return InlineKeyboardMarkup(navigation_buttons() + category_buttons("", user_id))


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("To do list bot started! Use /help to see available commands.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/add to add a task, /categories to see all categories, "
        "/expire to see expired tasks, /get to find all non expired tasks, "
        "/get_done to see done tasks, /delete to delete tasks, "
        "/help to see this message again!"
    )


async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = load_user(update.effective_chat.id)
    await update.message.reply_text(f"Available categories: {user['all_keys']}")


async def get_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Choose an option:", reply_markup=main_menu(update.effective_chat.id)
    )


async def get_done_tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    done_tasks = load_user(update.effective_chat.id).get("done", {})
    if not done_tasks:
        await update.message.reply_text("No done tasks.")
        return

    lines = ["Done tasks:"]
    for path, task in done_tasks.items():
        lines.append(f"Task: {path}\nDetails: {task['Todo']}\n")
    await update.message.reply_text("\n".join(lines))


async def get_expired_tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expired = load_user(update.effective_chat.id).get("expired", {})
    if not expired:
        await update.message.reply_text("No expired tasks.")
        return

    lines = ["Expired tasks:"]
    for path, task in expired.items():
        lines.append(f"Task: {path}\nDetails: {task['Todo']}\n")
    await update.message.reply_text("\n".join(lines))


async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = load_user(update.effective_chat.id)
    tasks = find_tasks(user["todolist"])
    if not tasks:
        await update.message.reply_text("No tasks available to delete.")
        return

    keyboard = [
        [InlineKeyboardButton(f"{split_path(path)[1]}: {task['Todo']}", callback_data=f"delete_{path}")]
        for path, task in tasks
    ]
    keyboard.append([InlineKeyboardButton("Delete All", callback_data="delete_all")])
    await update.message.reply_text("Select a task to delete:", reply_markup=InlineKeyboardMarkup(keyboard))


# --- /add conversation ------------------------------------------------------

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Please provide the details for your new task or if you want to leave use /leave."
    )
    return ADD_TASK_STATE


async def leave_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("You have left the conversation.")
    return ConversationHandler.END


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_data = add_task(update.message.text, update.effective_chat.id)
    await update.message.reply_text(f"Done! Added task with title '{task_data.Title}'")


# ---------------------------------------------------------------------------
# Button (callback query) handlers
# ---------------------------------------------------------------------------

async def show_main_menu(query, user_id):
    await query.edit_message_text(text="Choose an option:", reply_markup=main_menu(user_id))


async def delete_from_button(query, user_id, target):
    user = load_user(user_id)

    if target == "all":
        user["todolist"] = {}
        text = "All tasks have been deleted."
    else:
        title = split_path(target)[1]
        removed = pop_task(user["todolist"], target)
        text = f"Task '{title}' has been deleted." if removed else f"Task '{title}' not found."

    save_user(user_id, user)
    await query.edit_message_text(text=text)


async def mark_done_from_button(query, user_id, path):
    user = load_user(user_id)
    task = pop_task(user["todolist"], path)

    if task is None:
        text = f"Task not found: {split_path(path)[1]}"
    else:
        user["done"][path] = task
        save_user(user_id, user)
        text = f"Task marked as done: {split_path(path)[1]}"

    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(navigation_buttons()))


async def show_upcoming_task(query, user_id):
    keyboard = navigation_buttons()
    user = load_user(user_id)

    # Only consider tasks that haven't passed yet.
    upcoming = [
        (path, task) for path, task in find_tasks(user["todolist"])
        if hours_remaining(task) >= 0
    ]
    if not upcoming:
        await query.edit_message_text(
            text="No tasks available.", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    path, task = min(upcoming, key=lambda item: hours_remaining(item[1]))
    keyboard.append([InlineKeyboardButton("Mark as done", callback_data=f"done_task{path}")])
    await query.edit_message_text(
        text=format_task(split_path(path)[1], task),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_task_or_category(query, user_id, path):
    keyboard = navigation_buttons()
    node = get_node(load_user(user_id)["todolist"], path)

    if "Todo" in node:  # it's a task
        text = format_task(split_path(path)[1], node)
        keyboard.append([InlineKeyboardButton("Mark as done", callback_data=f"done_task{path}")])
    else:  # it's a category
        text = f"Selected category: {path}"
        keyboard.extend(category_buttons(path, user_id))

    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))


async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route every inline-button press to the right handler by its prefix."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_chat.id
    data = query.data

    if data == "return":
        await show_main_menu(query, user_id)
    elif data.startswith("delete_"):
        await delete_from_button(query, user_id, data[len("delete_"):])
    elif data.startswith("done_task"):
        await mark_done_from_button(query, user_id, data[len("done_task"):])
    elif data == "get_task_latest":
        await show_upcoming_task(query, user_id)
    elif data.startswith("get_task_"):
        await show_task_or_category(query, user_id, data[len("get_task_"):])


# ---------------------------------------------------------------------------
# Background job: move overdue tasks to "expired" and notify the user
# ---------------------------------------------------------------------------

async def check_expired_tasks(context: ContextTypes.DEFAULT_TYPE):
    for user_id in get_all_user_ids():
        user = load_user(user_id)
        changed = False

        for path, task in find_tasks(user["todolist"]):
            if hours_remaining(task) >= 0:
                continue

            task["expired"] = True
            user["expired"][path] = pop_task(user["todolist"], path)
            changed = True

            await context.application.bot.send_message(
                chat_id=user_id,
                text=f"The task '{split_path(path)[1]}' has expired!",
            )

        if changed:
            save_user(user_id, user)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print("Starting bot...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.job_queue.run_repeating(check_expired_tasks, interval=EXPIRY_CHECK_INTERVAL_SECONDS, first=10)

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("categories", categories_command))
    app.add_handler(CommandHandler("get", get_task_command))
    app.add_handler(CommandHandler("get_done", get_done_tasks_command))
    app.add_handler(CommandHandler("expire", get_expired_tasks_command))
    app.add_handler(CommandHandler("delete", delete_command))
    app.add_handler(CallbackQueryHandler(button_click))
    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("add", add_command)],
            states={ADD_TASK_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)]},
            fallbacks=[CommandHandler("leave", leave_command)],
        )
    )

    print("Running!~")
    app.run_polling()


if __name__ == "__main__":
    main()
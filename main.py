"""Slack to-do bot that uses an LLM (via Groq) to turn free text into tasks.

Everything is driven by one slash command, /todo:
    /todo add <task description>
    /todo get | get_done | expired | categories | delete | help

Tasks are stored per user in data/<slack_user_id>.json as a tree:
    todolist -> category -> subcategory -> ... -> {task title: task entry}
A task entry is any dict containing the key "Todo".
"""

import datetime
import json
import os
import re
import threading
import time
from datetime import date

from dotenv import load_dotenv
from groq import Groq
from pydantic import BaseModel
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SLACK_API_KEY = os.getenv("SLACK_API_KEY")      # Bot token (xoxb-...)
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")  # App-level token (xapp-...), needed for Socket Mode

DATA_DIR = "data"
LLM_MODEL = "openai/gpt-oss-20b"
EXPIRY_CHECK_INTERVAL_SECONDS = 600  # every 10 minutes
MAX_BUTTONS = 45  # Slack allows at most 50 blocks per message

HELP_TEXT = (
    "`/todo add <task>` add a task\n"
    "`/todo get` browse your tasks\n"
    "`/todo categories` see all categories\n"
    "`/todo expired` see expired tasks\n"
    "`/todo get_done` see done tasks\n"
    "`/todo delete` delete tasks\n"
    "`/todo help` show this message"
)

client = Groq(api_key=GROQ_API_KEY)
app = App(token=SLACK_API_KEY)


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
    return [name[:-5] for name in os.listdir(DATA_DIR) if name.endswith(".json")]


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
        f"*Selected task:*\n{title}: {task['Todo']}\n"
        f"Due Date: {task['DueDate']} Time: {task['Time']}\n"
        f"Hours remaining: {hours_remaining(task):.1f}"
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
# Message / button building (Slack Block Kit)
# ---------------------------------------------------------------------------

def button(label, action_id, value=""):
    """One button, in its own 'actions' block so it sits on its own row."""
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": label[:75]},
                "action_id": action_id,
                "value": value or "-",
            }
        ],
    }


def show(respond, text, buttons=()):
    """Send (or replace) a message made of some text plus button rows."""
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    blocks.extend(list(buttons)[:MAX_BUTTONS])
    respond(text=text, blocks=blocks, replace_original=True)


def navigation_buttons():
    """The 'Return' and 'Get upcoming task' rows shown on most menus."""
    return [
        button("Return", "return"),
        button("Get upcoming task", "upcoming"),
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
        buttons.append(button(name, f"get_task:{child_path}", child_path))
    return buttons


def main_menu_buttons(user_id):
    return navigation_buttons() + category_buttons("", user_id)


# ---------------------------------------------------------------------------
# /todo subcommands
# ---------------------------------------------------------------------------

def cmd_help(user_id, args, respond):
    respond(HELP_TEXT)


def cmd_add(user_id, args, respond):
    if not args:
        respond("Please describe your task, e.g. `/todo add finish report by friday 5pm`")
        return

    try:
        task_data = add_task(args, user_id)
    except Exception as error:  # LLM/JSON/validation problems
        respond(f"Sorry, I couldn't add that task: {error}")
        return

    respond(f"Done! Added task with title '{task_data.Title}'")


def cmd_categories(user_id, args, respond):
    respond(f"Available categories: {load_user(user_id)['all_keys']}")


def cmd_get(user_id, args, respond):
    show(respond, "Choose an option:", main_menu_buttons(user_id))


def format_task_archive(heading, tasks):
    lines = [heading]
    for path, task in tasks.items():
        lines.append(f"Task: {path}\nDetails: {task['Todo']}\n")
    return "\n".join(lines)


def cmd_get_done(user_id, args, respond):
    done = load_user(user_id).get("done", {})
    respond(format_task_archive("*Done tasks:*", done) if done else "No done tasks.")


def cmd_expired(user_id, args, respond):
    expired = load_user(user_id).get("expired", {})
    respond(format_task_archive("*Expired tasks:*", expired) if expired else "No expired tasks.")


def cmd_delete(user_id, args, respond):
    tasks = find_tasks(load_user(user_id)["todolist"])
    if not tasks:
        respond("No tasks available to delete.")
        return

    buttons = [
        button(f"{split_path(path)[1]}: {task['Todo']}", f"delete:{path}", path)
        for path, task in tasks
    ]
    buttons.append(button("Delete All", "delete_all"))
    show(respond, "Select a task to delete:", buttons)


SUBCOMMANDS = {
    "help": cmd_help,
    "add": cmd_add,
    "categories": cmd_categories,
    "get": cmd_get,
    "get_done": cmd_get_done,
    "expired": cmd_expired,
    "delete": cmd_delete,
}


@app.command("/tyotodo")
def handle_todo_command(ack, command, respond):
    ack()  # Slack requires an acknowledgement within 3 seconds

    name, _, args = command["text"].strip().partition(" ")
    handler = SUBCOMMANDS.get(name.lower() or "help")
    if handler is None:
        respond(f"Unknown command `{name}`.\n{HELP_TEXT}")
        return

    handler(command["user_id"], args.strip(), respond)


# ---------------------------------------------------------------------------
# Button click handlers
# ---------------------------------------------------------------------------

@app.action("return")
def on_return(ack, body, respond):
    ack()
    show(respond, "Choose an option:", main_menu_buttons(body["user"]["id"]))


@app.action("upcoming")
def on_upcoming(ack, body, respond):
    ack()
    user = load_user(body["user"]["id"])

    # Only consider tasks that haven't passed yet.
    upcoming = [
        (path, task) for path, task in find_tasks(user["todolist"])
        if hours_remaining(task) >= 0
    ]
    if not upcoming:
        show(respond, "No tasks available.", navigation_buttons())
        return

    path, task = min(upcoming, key=lambda item: hours_remaining(item[1]))
    buttons = navigation_buttons() + [button("Mark as done", f"done_task:{path}", path)]
    show(respond, format_task(split_path(path)[1], task), buttons)


@app.action(re.compile(r"^get_task:"))
def on_get_task(ack, body, action, respond):
    ack()
    user_id = body["user"]["id"]
    path = action["value"]
    node = get_node(load_user(user_id)["todolist"], path)

    buttons = navigation_buttons()
    if "Todo" in node:  # it's a task
        text = format_task(split_path(path)[1], node)
        buttons.append(button("Mark as done", f"done_task:{path}", path))
    else:  # it's a category
        text = f"*Selected category:* {path}"
        buttons.extend(category_buttons(path, user_id))

    show(respond, text, buttons)


@app.action(re.compile(r"^done_task:"))
def on_done_task(ack, body, action, respond):
    ack()
    user_id = body["user"]["id"]
    path = action["value"]

    user = load_user(user_id)
    task = pop_task(user["todolist"], path)
    title = split_path(path)[1]

    if task is None:
        text = f"Task not found: {title}"
    else:
        user["done"][path] = task
        save_user(user_id, user)
        text = f"Task marked as done: {title}"

    show(respond, text, navigation_buttons())


@app.action(re.compile(r"^delete:"))
def on_delete_task(ack, body, action, respond):
    ack()
    user_id = body["user"]["id"]
    path = action["value"]

    user = load_user(user_id)
    removed = pop_task(user["todolist"], path)
    title = split_path(path)[1]
    save_user(user_id, user)

    show(respond, f"Task '{title}' has been deleted." if removed else f"Task '{title}' not found.")


@app.action("delete_all")
def on_delete_all(ack, body, respond):
    ack()
    user_id = body["user"]["id"]

    user = load_user(user_id)
    user["todolist"] = {}
    save_user(user_id, user)

    show(respond, "All tasks have been deleted.")


# ---------------------------------------------------------------------------
# Background job: move overdue tasks to "expired" and notify the user
# ---------------------------------------------------------------------------

def check_expired_tasks():
    for user_id in get_all_user_ids():
        user = load_user(user_id)
        changed = False

        for path, task in find_tasks(user["todolist"]):
            if hours_remaining(task) >= 0:
                continue

            task["expired"] = True
            user["expired"][path] = pop_task(user["todolist"], path)
            changed = True

            # Posting to a user ID sends a DM from the bot.
            app.client.chat_postMessage(
                channel=user_id,
                text=f"The task '{split_path(path)[1]}' has expired!",
            )

        if changed:
            save_user(user_id, user)


def expiry_loop():
    time.sleep(10)
    while True:
        try:
            check_expired_tasks()
        except Exception as error:  # keep the thread alive no matter what
            print(f"Expiry check failed: {error}")
        time.sleep(EXPIRY_CHECK_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print("Starting bot...")
    threading.Thread(target=expiry_loop, daemon=True).start()

    print("Running!~")
    SocketModeHandler(app, SLACK_APP_TOKEN).start()


if __name__ == "__main__":
    main()
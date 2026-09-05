import os
import threading
from pydantic import BaseModel, ValidationError
import time
from datetime import date
import datetime
from groq import Groq
import json
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from dotenv import load_dotenv
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
)

load_dotenv()

todolist = {}
all_keys = []

api_key = os.getenv("GROQ_API_KEY")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


client = Groq(api_key=api_key)

def getkeys(index: int):
    temp = set()
    for i in all_keys:
        keys = i.split("/")
        if len(keys) > index:
            temp.add(keys[index])
    return temp


class Structure (BaseModel):
    Todo: str
    Title: str
    DueDate: str
    Time: str
    Sort: str
    done: bool = False
client = Groq(api_key=api_key)
text = ""
chat_ids = set()

def hoursremain(task):
    if task.get("DueDate", "") == "TBD":
        return float("inf")  # Return infinity if the due date is TBD
    task_date = datetime.datetime.strptime(task.get("DueDate", "") + task.get("Time", ""), "%Y-%m-%d%H:%M")
    current_date = datetime.datetime.now()
    delta = task_date - current_date
    return delta.total_seconds() / 3600

def task(text:str):
    response = client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a todo list sorter. Today's date is {date.today()}."
                    " Extract the task details from the user's text and output a VALID JSON object matching this schema exactly:\n"
                    '{"Todo": "string", "Title": "string", "DueDate": "YYYY-MM-DD", "Time": "HH:MM", "Sort": "category/subcategory", "done": "boolean"}\n'
                    f"The 'Sort' field should be a string that represents the category and subcategory of the task, separated by a forward slash. If there is no subcategory, just provide the category. It can also have multiple subcategories, separated by forward slashes. Currently, the categories are: {str(all_keys)}, You may create new categories but only if you think it's necessary.\n"
                    "If you think the description does not give a time, assume it as '00:00'. If you think the description does not give a due date, please write 'TBD'"
                    " Do not return any pleasantries, introduction, or conversational filler. Return ONLY the raw JSON object."
                ),
            },
            {
                "role": "user",
                "content": f"Extract task details from: '{text}'",
            },
        ],
        response_format={"type": "json_object"},  # Forces Groq to output raw JSON
    )

    # 1. Grab the raw text string from the response
    raw_json_string = response.choices[0].message.content

    # 2. Parse the string into a standard Python dictionary
    parsed_dict = json.loads(raw_json_string)

    task_data = Structure(**parsed_dict)
    if task_data.Sort not in all_keys:
        all_keys.append(task_data.Sort)
    print(task_data)
    
    sorting = task_data.Sort.split("/")
    title = task_data.Title
    temp = {"Todo": task_data.Todo, "DueDate": task_data.DueDate, "Time": task_data.Time, "done": False, "expired": False}
    def add_task_to_todolist(todolist, sorting, title, task):
        current = todolist
        for category in sorting:
            if category not in current:
                current[category] = {}
            current = current[category]
        current[title] = task
    found = add_task_to_todolist(todolist, sorting, title, temp)
    print(f"Found: {found}")

    print("Task added successfully! Here is your updated todo list:")
    print(todolist)
    return {title: temp}

async def get_expired_tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! I am your Python bot. How can I help you today?")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_ids.add(update.effective_chat.id)
    await update.message.reply_text("To do list bot started! Use /help to see available commands.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/add to add a task, /categories to see all categories, /help to see this message again!")

async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Available categories: {str(all_keys)}")

def category_exists(index: str):
    if index == "":
        return ([InlineKeyboardButton(category, callback_data=f"get_task_{category}")] for category in list(todolist.keys()))
    tempindex = index
    index = index.split("/")
    temp = todolist
    for i in index:
        temp = temp.get(i, {})
    return ([InlineKeyboardButton(category, callback_data=f"get_task_{tempindex}/{category}")] if not temp[category].get("done", False) and not temp[category].get("expired", False) else [] for category in list(temp.keys()))

async def get_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("Return", callback_data="return"),
            
        ],
        [InlineKeyboardButton("Get latest task", callback_data="get_task_latest")]
    ]
    keyboard.extend(category_exists(""))
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Choose an option:", reply_markup=reply_markup)


async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Please provide the details for your new task or if you want to leave use /quit.")
    return 1 


async def leave_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("You have left the conversation.")
    return ConversationHandler.END

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer() 
    
    
    if query.data == "return":
        keyboard = [
            [
                InlineKeyboardButton("Return", callback_data="return"),
                
            ],
            [InlineKeyboardButton("Get latest task", callback_data="get_task_latest")]
        ]
        keyboard.extend(category_exists(""))
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text="Choose an option:", reply_markup=reply_markup)
    elif "done_task" in query.data:
        curent = query.data.split("done_task")[1]
        index = curent.split("/")
        temp = todolist
        for i in index:
            temp = temp.get(i, {})
        temp["done"] = True
        text = f"Task marked as done: {index[-1]}"
        keyboard = [
            [
                InlineKeyboardButton("Return", callback_data="return"),
                
            ],
            [InlineKeyboardButton("Get latest task", callback_data="get_task_latest")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup)

    elif "get_task_" in query.data:
        text = ""
        curent = query.data.split("get_task_")[1]
        
        index = curent.split("/")
        temp = todolist
        keyboard = [
            [
                InlineKeyboardButton("Return", callback_data="return"),
                
            ],
            [InlineKeyboardButton("Get latest task", callback_data="get_task_latest")]
        ]
        for i in index:
            temp = temp.get(i, {})
        if "Todo" in temp:
            tasks = f"\n{index[-1]}: {temp["Todo"]}"
            text=f"Selected task:{tasks}"
            keyboard.append([InlineKeyboardButton("Mark as done", callback_data=f"done_task{curent}")])
        else:
            text = f"Selected category: {curent}"
            keyboard.extend(category_exists(curent))

        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text=text, reply_markup=reply_markup)



# 2. Define a message handler (Processes regular text messages)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    result = task(user_text)
    await update.message.reply_text(f"Done! Added task with title '{list(result.keys())[0]}'")

print("System ready awaiting input")

def get_all_tasks(data):
    tasks = []
    for key, value in data.items():
        if isinstance(value, dict):
            if "Todo" in value:
                tasks.append((key, value))
            else:
                tasks.extend(get_all_tasks(value))
    return tasks

async def check(context):
    all_tasks = get_all_tasks(todolist)
    for task in all_tasks:
        task_date = hoursremain(task[1])
        if task_date < 0 and not task[1].get("expired", False):
            task[1]["expired"] = True
            for i in chat_ids:
                await context.application.bot.send_message(
                chat_id=i,
                text=f"The task '{task[0]}' has expired!"
                )
    for i in chat_ids:            
        await context.application.bot.send_message(
        chat_id=i,
        text=f"Updating tasks... Current tasks: {str(todolist)}"
        )
            



def main():
    print("Starting bot...")
    app = Application.builder().token(TOKEN).build()
    
    app.job_queue.run_repeating(
        check,
        interval=600, # every 10 minutes
        first=10
        )

    # Register Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("categories", categories_command))
    app.add_handler(CommandHandler("get", get_task_command))
    app.add_handler(CommandHandler("expire", get_expired_tasks_command))
    app.add_handler(CallbackQueryHandler(button_click))
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_command)],
        states={
            1: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
        },
        fallbacks=[CommandHandler("leave", leave_command)],
    )
    app.add_handler(conv_handler)

    #app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))


    print("Running!~")
    app.run_polling()

if __name__ == "__main__":
    main()
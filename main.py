import os
from pydantic import BaseModel, ValidationError
import time
from datetime import date
from groq import Groq
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes


todolist = {}
all_keys = []

api_key = os.environ.get("GROQ_API_KEY")
if not api_key:
    api_key = "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"

TOKEN = "8985295869:AAH8YG6WlvJiUbbOkyLAPsq2ulET8LWu2Pg"

client = Groq(api_key=api_key)




class Structure (BaseModel):
    Todo: str
    Title: str
    DueDate: str
    Time: str
    Sort: str
client = Groq(api_key=api_key)
text = ""


def task(text:str):
    response = client.chat.completions.create(
        model="openai/gpt-oss-20b",
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a todo list sorter. Today's date is {date.today()}."
                    " Extract the task details from the user's text and output a VALID JSON object matching this schema exactly:\n"
                    '{"Todo": "string", "Title": "string", "DueDate": "YYYY-MM-DD", "Time": "HH:MM", "Sort": "category/subcategory"}\n'
                    f"The 'Sort' field should be a string that represents the category and subcategory of the task, separated by a forward slash. If there is no subcategory, just provide the category. It can also have multiple subcategories, separated by forward slashes. Currently, the categories are: {str(all_keys)}, You may create new categories but only if you think it's necessary.\n"
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
    temp = {"Todo": task_data.Todo, "DueDate": task_data.DueDate, "Time": task_data.Time}
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
    return todolist

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello! I am your Python bot. How can I help you today?")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("I can repeat whatever you say! Just type a message.")

async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Available categories: {str(all_keys)}")

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        full_text = " ".join(context.args)
        task(full_text)
        await update.message.reply_text("Done!" + str(todolist))
    else:
        await update.message.reply_text("Please provide the details for your new task and call the function again.")

# 2. Define a message handler (Processes regular text messages)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Echoes back the text the user sent."""
    user_text = update.message.text
    await update.message.reply_text(f"You said: '{user_text}'")

print("System ready awaiting input")
def main():
    print("Starting bot...")
    
    # Build the application using your bot token
    app = Application.builder().token(TOKEN).build()

    # Register Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("categories", categories_command))
    app.add_handler(CommandHandler("add", add_command))

    # Register Message Handler (Filters for normal text messages, ignores commands)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Tell the bot to continuously check for updates from Telegram
    print("Bot is polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
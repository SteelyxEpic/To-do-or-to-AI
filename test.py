from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
async def hi_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 1. Create the UI buttons
    keyboard = [
        [
            InlineKeyboardButton("Do Nothing 😴", callback_data="do_nothing"),
            InlineKeyboardButton("Get Help ❓", callback_data="get_help"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # 2. Send the message with the UI attached
    await update.message.reply_text("hello! what do you want to do today?", reply_markup=reply_markup)

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer() # Acknowledge the click to stop the loading spinner
    
    # 3. Read which button was clicked
    if query.data == "do_nothing":
        await query.edit_message_text(text="ok~")
    elif query.data == "get_help":
        await query.edit_message_text(text="Here is how I can help you...")

def main():
    application = Application.builder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("hi", hi_command))
    application.add_handler(CallbackQueryHandler(button_click)) # Handles the button actions
    
    application.run_polling()

if __name__ == "__main__":
    main()
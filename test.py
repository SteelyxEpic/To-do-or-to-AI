import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Enable logging to see errors in the console
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Your Bot Token from BotFather
TOKEN = "8985295869:AAH8YG6WlvJiUbbOkyLAPsq2ulET8LWu2Pg"

# 1. Define the command handlers (Functions triggered by commands like /start)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcoming message when the user types /start."""
    await update.message.reply_text("Hello! I am your Python bot. How can I help you today?")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a help message when the user types /help."""
    await update.message.reply_text("I can repeat whatever you say! Just type a message.")

# 2. Define a message handler (Processes regular text messages)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Echoes back the text the user sent."""
    user_text = update.message.text
    await update.message.reply_text(f"You said: '{user_text}'")

# 3. Main function to initialize and run the bot
def main():
    print("Starting bot...")
    
    # Build the application using your bot token
    app = Application.builder().token(TOKEN).build()

    # Register Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))

    # Register Message Handler (Filters for normal text messages, ignores commands)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Tell the bot to continuously check for updates from Telegram
    print("Bot is polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
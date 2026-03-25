from purewaterbot.bot import BotEngine
from purewaterbot.llm import OllamaBot
import os
import sys

engine = BotEngine()
bot = OllamaBot(engine)
reply = bot.process_user_message("test_user_gemini", "whats available?")
print("BOT REPLY:", reply)

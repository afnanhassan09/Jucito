from pathlib import Path

from dotenv import load_dotenv

from purewaterbot.bot import BotEngine
from purewaterbot.llm import LLMBot

load_dotenv()

repo_root = Path(__file__).resolve().parent
engine = BotEngine.from_repo_root(repo_root)
bot = LLMBot(engine)
reply = bot.process_user_message("test_user_openai", "whats available?")
print("BOT REPLY:", reply)

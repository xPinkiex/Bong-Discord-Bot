import discord
import os
import sys
import importlib
from dotenv import load_dotenv
from discord.ext import commands
import debug

# Load environment variables from .env file
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise ValueError("No DISCORD_TOKEN found in environment variables!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="@", case_insensitive=True, intents=intents)

@bot.event
async def on_ready():
    debug.log("Bot", 'Bot booted, loading extensions...')
    await bot.load_extension('bong')
    debug.log("Bot", f'Bot logged in as {bot.user}')

@bot.command(name='reload')
@commands.is_owner()
async def reload_ext(ctx, util: str = "bong"):
    try:
        for mod in [util, util + "_tools", "debug"]:
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])
            debug.log("Bot", f"Reloaded module {mod}")
        for mod_name in list(sys.modules):
            if mod_name.startswith(util + ".") or (mod_name != util and util in mod_name.split(".")):
                importlib.reload(sys.modules[mod_name])
                debug.log("Bot", f"Reloaded submodule {mod_name}")

        await bot.unload_extension(util)
        debug.log("Bot", f"Unloaded extension {util}")
        
        await bot.load_extension(util)
        debug.log("Bot", f"Reloaded extension {util}")
        
        await ctx.message.delete()
    except commands.ExtensionNotLoaded:
        pass
    except Exception as e:
        await ctx.send(f"Error reloading extension: {e}")

@bot.command(name='load')
@commands.is_owner()
async def load_ext(ctx, util: str):
    try:
        await bot.load_extension(util)
        debug.log("Bot", f"Loaded extension {util}")
        await ctx.send(f"Extension {util} loaded successfully!")

    except Exception as e:
        await ctx.send(f"Error loading extension: {e}")

@bot.command(name='unload')
@commands.is_owner()
async def unload_ext(ctx, util: str):
    try:
        await bot.unload_extension(util)
        debug.log("Bot", f"Unloaded extension {util}")
        await ctx.send(f"Extension {util} unloaded successfully!")

    except Exception as e:
        await ctx.send(f"Error unloading extension: {e}")

@bot.command(name='poweroff', help="Power off the bot")
@commands.is_owner()
async def poweroff(ctx):
    await ctx.send("Onoffing...")
    await bot.close()

@bot.command(name='debug', help="Toggle debug mode")
@commands.is_owner()
async def toggle_debug(ctx, enabled: bool = None):
    if enabled is None:
        enabled = not debug.toggle_debug()
    debug.toggle_debug(enabled)
    await ctx.send(f"Debug mode {'enabled' if enabled else 'disabled'}")

bot.run(TOKEN)
import discord
from discord.ext import commands, tasks
import json
import re
from datetime import datetime, timedelta
import asyncio
from discord import AllowedMentions
import os
import dotenv
from dotenv import load_dotenv

load_dotenv()


# ---------- CONFIG ----------
LOG_CHANNEL_ID = 1513699221876768788  # put your mod-log channel ID here
INVITE_LINK = "https://discord.gg/wBavfmnQgH"  # replace with your invite

PREFIX = "!"

# ---------------- FILES ----------------
data_file = os.getenv("data_file", "/data")
os.makedirs(data_file, exist_ok=True)
WARN_FILE = os.path.join(data_file, "warnings.json")
# ----------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# simple persistent warnings store
def load_warnings():
    try:
        with open(WARN_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_warnings(data):
    with open(WARN_FILE, "w") as f:
        json.dump(data, f, indent=2)

# helper to get log channel
def get_log_channel(guild):
    return guild.get_channel(LOG_CHANNEL_ID) or bot.get_channel(LOG_CHANNEL_ID)

# parse ban duration strings like "1 day", "2 days", "1 month", "perm"
def parse_duration(text: str):
    text = text.strip().lower()
    if text in ("perm", "permanent", "perma"):
        return None  # indicate permanent
    m = re.match(r"(\d+)\s*(day|days|d|hour|hours|h|minute|minutes|min|m|month|months)", text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit.startswith("month"):
        days = n * 30
        return timedelta(days=days)
    if unit.startswith("day") or unit == "d":
        return timedelta(days=n)
    if unit.startswith("hour") or unit == "h":
        return timedelta(hours=n)
    if unit.startswith("minute") or unit.startswith("min") or unit == "m":
        return timedelta(minutes=n)
    return None

# embed white color
WHITE = discord.Color.from_rgb(255, 255, 255)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")

# KICK
@bot.command(name="kick")
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    await member.kick(reason=reason)
    await ctx.send(f"{member.mention} has been kicked.")
    # Log
    log_chan = get_log_channel(ctx.guild)
    if log_chan:
        await log_chan.send(f"{ctx.author} kicked {member}.")
    # Optional: DM the user
    try:
        await member.send(f"You have been kicked from {ctx.guild.name}. Reason: {reason}")
    except:
        pass

def get_log_channel(guild):
    return guild.get_channel(LOG_CHANNEL_ID)

def extract_id(s: str):
    m = re.search(r"(\d{17,20})", s)
    return int(m.group(1)) if m else None

# BAN with optional duration (uses seconds param)
@bot.command(name="ban")
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, duration: str = "perm", *, reason: str = "No reason provided"):
    try:
        await ctx.message.delete()
    except:
        pass

    duration_delta = parse_duration(duration)  # keep your parse_duration
    log_chan = get_log_channel(ctx.guild)

    # fetch user object so DMs work after ban
    user_obj = None
    try:
        user_obj = await bot.fetch_user(member.id)
    except Exception as e:
        if log_chan:
            await log_chan.send(f"Failed to fetch user object for {member.id} before ban: {e}")

    # perform ban (use delete_message_seconds)
    try:
        await ctx.guild.ban(member, reason=reason, delete_message_seconds=0)
    except Exception as e:
        if log_chan:
            await log_chan.send(f"{ctx.author} attempted to ban {member} but failed: {e}")
        return

    # DM user if possible
    if user_obj:
        try:
            await user_obj.send(f"You have been banned from {ctx.guild.name}. Time: {duration}. Reason: {reason}")
        except discord.Forbidden:
            if log_chan:
                await log_chan.send(f"Could not DM <@{member.id}> upon ban: Forbidden.")
        except Exception as e:
            if log_chan:
                await log_chan.send(f"Could not DM <@{member.id}> upon ban: {e}")

    # log to mod-log only
    if log_chan:
        await log_chan.send(f"{ctx.author} banned <@{member.id}> (time: {duration}; reason: {reason})")

    # schedule unban if temporary
    if duration_delta is not None:
        user_id = member.id
        async def temp_unban():
            await asyncio.sleep(duration_delta.total_seconds())
            try:
                await ctx.guild.unban(discord.Object(id=user_id), reason="Temporary ban expired")
                if log_chan:
                    await log_chan.send(f"<@{user_id}> has been automatically unbanned after {duration}.")
                # try DM after unban
                try:
                    u = await bot.fetch_user(user_id)
                    await u.send(f"You have been unbanned from {ctx.guild.name} (temporary ban expired). Invite: {INVITE_LINK}")
                except discord.Forbidden:
                    if log_chan:
                        await log_chan.send(f"Could not DM <@{user_id}> after auto-unban: Forbidden. Invite posted in mod-log.")
                        await log_chan.send(f"Invite for <@{user_id}>: {INVITE_LINK}")
                except Exception as e:
                    if log_chan:
                        await log_chan.send(f"Could not DM <@{user_id}> after auto-unban: {e}. Invite posted in mod-log.")
                        await log_chan.send(f"Invite for <@{user_id}>: {INVITE_LINK}")
            except Exception as e:
                if log_chan:
                    await log_chan.send(f"Failed to auto-unban {user_id}: {e}")
        bot.loop.create_task(temp_unban())

# UNBAN (accepts ID or mention), deletes invoking message, logs only to mod-log
@bot.command(name="unban")
@commands.has_permissions(ban_members=True)
async def unban(ctx, user: str):
    try:
        await ctx.message.delete()
    except:
        pass

    user_id = extract_id(user)
    log_chan = get_log_channel(ctx.guild)
    if not user_id:
        if log_chan:
            await log_chan.send(f"{ctx.author} attempted unban with invalid identifier: {user}")
        return

    obj = discord.Object(id=user_id)

    # attempt to fetch user early for DM attempt
    fetched_user = None
    try:
        fetched_user = await bot.fetch_user(user_id)
    except Exception as e:
        if log_chan:
            await log_chan.send(f"Failed to fetch user {user_id} before unban: {e}")

    # perform unban
    try:
        await ctx.guild.unban(obj)
    except Exception as e:
        if log_chan:
            await log_chan.send(f"{ctx.author} attempted to unban {user_id} but failed: {e}")
        return

    # log to mod-log only
    if log_chan:
        await log_chan.send(f"{ctx.author} has unbanned <@{user_id}>.")

    # try DM after unban (prefer fetched_user)
    try:
        u = fetched_user or await bot.fetch_user(user_id)
        await u.send(f"You have been unbanned from {ctx.guild.name}. Invite: {INVITE_LINK}")
    except discord.Forbidden:
        if log_chan:
            await log_chan.send(f"Could not DM <@{user_id}> after unban: Forbidden (user blocked DMs or blocked the bot). Invite posted in mod-log.")
            await log_chan.send(f"Invite for <@{user_id}>: {INVITE_LINK}")
    except Exception as e:
        if log_chan:
            await log_chan.send(f"Could not DM <@{user_id}> after unban: {e}. Invite posted in mod-log.")
            await log_chan.send(f"Invite for <@{user_id}>: {INVITE_LINK}")

# CLEARWARNINGS (example command) - delete invocation, no public send, only mod-log
@bot.command(name="clearwarnings")
@commands.has_permissions(manage_messages=True)
async def clearwarnings(ctx, user: str):
    try:
        await ctx.message.delete()
    except:
        pass

    user_id = extract_id(user) or None
    log_chan = get_log_channel(ctx.guild)

    # perform your warnings removal logic here; example assumes function remove_warnings(guild, user_id)
    try:
        removed_for = user_id
        # remove_warnings(ctx.guild, user_id)
    except Exception as e:
        if log_chan:
            await log_chan.send(f"{ctx.author} attempted to clear warnings for {user} but failed: {e}")
        return

    # only send result to mod-log
    if log_chan:
        await log_chan.send(f"Warnings removed for user ID {removed_for}.")

# WARN
@bot.command(name="warn")
@commands.has_permissions(manage_messages=True)
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    data = load_warnings()
    guild_id = str(ctx.guild.id)
    data.setdefault(guild_id, {})
    data[guild_id].setdefault(str(member.id), [])
    entry = {"by": str(ctx.author), "reason": reason, "time": datetime.utcnow().isoformat()}
    data[guild_id][str(member.id)].append(entry)
    save_warnings(data)
    await ctx.send(f"{member.mention} has been warned.")
    # DM the user
    try:
        await member.send(f"{member.mention} You Have been warned from {ctx.guild.name} because: {reason}")
    except:
        pass
    # Log
    log_chan = get_log_channel(ctx.guild)
    if log_chan:
        await log_chan.send(f"{ctx.author} warned {member}. Reason: {reason}")

# UNWARN
@bot.command(name="unwarn")
@commands.has_permissions(manage_messages=True)
async def unwarn(ctx, user_id: int):
    data = load_warnings()
    guild_id = str(ctx.guild.id)
    removed = False
    if guild_id in data and str(user_id) in data[guild_id]:
        data[guild_id].pop(str(user_id), None)
        save_warnings(data)
        removed = True
    if removed:
        await ctx.send(f"Warnings removed for user ID {user_id}.")
        log_chan = get_log_channel(ctx.guild)
        if log_chan:
            await log_chan.send(f"{ctx.author} removed warnings for {user_id}.")
    else:
        await ctx.send("No warnings found for that user.")

# MUTE (timeout)
@bot.command(name="mute")
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, *, reason: str = "No reason provided"):
    # Default 1 hour timeout if not supported? We will set to 1 day by default if no duration given
    try:
        # Put them in timeout for 1 day by default
        until = discord.utils.utcnow() + timedelta(days=1)
        await member.edit(timed_out_until=until, reason=reason)
        await ctx.send(f"{member.mention} has been timed out.")
        log_chan = get_log_channel(ctx.guild)
        if log_chan:
            await log_chan.send(f"{ctx.author} timed out {member}. Reason: {reason}")
    except Exception as e:
        await ctx.send(f"Could not timeout member: {e}")

# UNMUTE
@bot.command(name="unmute")
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member):
    try:
        await member.edit(timed_out_until=None, reason=f"Unmuted by {ctx.author}")
        await ctx.send(f"{member.mention} has been removed from timeout.")
        log_chan = get_log_channel(ctx.guild)
        if log_chan:
            await log_chan.send(f"{ctx.author} removed timeout for {member}.")
    except Exception as e:
        await ctx.send(f"Could not remove timeout: {e}")


def get_log_channel(guild):
    return guild.get_channel(LOG_CHANNEL_ID)


# allow everyone and role pings (set users=True if you want user mentions to ping)
ALLOWED = AllowedMentions(everyone=True, roles=True, users=True)
NO_PINGS_LOG = AllowedMentions.none()

ZERO = chr(0x200B)

def normalize_mentions(text: str) -> str:
    # turn "@ everyone" / "@  here" into proper "@everyone" / "@here"
    text = re.sub(r"@\s*everyone", "@everyone", text, flags=re.IGNORECASE)
    text = re.sub(r"@\s*here", "@here", text, flags=re.IGNORECASE)
    # leave other spaced role/user names alone (can't reliably convert plain names to role mentions)
    return text

def get_log_channel(guild):
    return guild.get_channel(LOG_CHANNEL_ID)

@bot.command(name="announce")
@commands.has_permissions(administrator=True)
async def announce(ctx, channel: discord.TextChannel, *, content: str):
    # parse optional leading seconds
    parts = content.split(None, 1)
    delete_after = 0
    message_text = content
    if parts:
        try:
            seconds = int(parts[0])
            if len(parts) > 1:
                delete_after = max(0, seconds)
                message_text = parts[1]
        except ValueError:
            pass

    # normalize and send allowing pings
    message_text = normalize_mentions(message_text)
    sent = await channel.send(message_text, allowed_mentions=ALLOWED)

    # log only to mod-log without pings
    log_chan = get_log_channel(ctx.guild)
    if log_chan:
        await log_chan.send(f"Announcement sent to {channel.mention}", allowed_mentions=NO_PINGS_LOG)

    try:
        await ctx.message.delete()
    except:
        pass

    if delete_after > 0:
        await asyncio.sleep(delete_after)
        try:
            await sent.delete()
        except:
            pass

# USERINFO (private to command author)
@bot.command(name="userinfo")
@commands.has_permissions(view_audit_log=True)
async def userinfo(ctx, user: str):
    # delete the invoking command message (requires Manage Messages)
    try:
        await ctx.message.delete()
    except Exception:
        pass

    # Accept mention, ID, or member
    target = None
    try:
        if user.isdigit():
            target = await bot.fetch_user(int(user))
            # try to get member in guild
            member = ctx.guild.get_member(int(user))
        else:
            # mention or name
            m = re.search(r"\d{17,20}", user)
            if m:
                uid = int(m.group(0))
                target = await bot.fetch_user(uid)
                member = ctx.guild.get_member(uid)
            else:
                # try resolve by name
                member = discord.utils.find(lambda m: m.name == user or m.display_name == user, ctx.guild.members)
                target = member or None
        if isinstance(target, discord.Object) and not hasattr(target, "id"):
            # fetch user
            target = await bot.fetch_user(int(user))
    except Exception:
        member = None

    if not target and not member:
        try:
            await ctx.author.send("User not found.")
        except:
            pass
        try:
            await ctx.message.add_reaction("❌")
        except:
            pass
        return

    # prefer member for joined_at and roles
    if isinstance(member, discord.Member):
        joined = member.joined_at.isoformat() if member.joined_at else "Unknown"
        roles = [r.name for r in member.roles if r != ctx.guild.default_role]
        banned = False
        try:
            bans = await ctx.guild.bans()
            banned = any(b.user.id == member.id for b in bans)
        except:
            banned = False
        embed = discord.Embed(title=str(member), color=WHITE)
        embed.add_field(name="Joined", value=joined, inline=False)
        embed.add_field(name="Roles", value=", ".join(roles) or "None", inline=False)
        embed.add_field(name="Banned", value=str(banned), inline=False)
        try:
            await ctx.author.send(embed=embed)
        except:
            pass
    else:
        # user object only
        u = target
        banned = False
        try:
            bans = await ctx.guild.bans()
            banned = any(b.user.id == u.id for b in bans)
        except:
            banned = False
        embed = discord.Embed(title=str(u), color=WHITE)
        embed.add_field(name="Joined", value="Not in guild", inline=False)
        embed.add_field(name="Roles", value="Not in guild", inline=False)
        embed.add_field(name="Banned", value=str(banned), inline=False)
        try:
            await ctx.author.send(embed=embed)
        except:
            pass

    try:
        await ctx.message.add_reaction("✅")
    except:
        pass

# Basic error feedback
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to run this command.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Invalid argument.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing argument.")
    else:
        # For other exceptions, show brief info
        await ctx.send(f"Error: {getattr(error, 'original', error)}")

if __name__ == "__main__":
    bot.run(os.getenv("BOT_TOKEN"))

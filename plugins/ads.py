from pyrogram import Client, filters
from datetime import datetime, timedelta
from database.config_db import mdb
from database.users_chats_db import db
from info import ADMINS
import asyncio
import re


@Client.on_message(filters.private & filters.command("set_ads") & filters.user(ADMINS))
async def set_ads(client, message):
    try:
        if len(message.command) < 2:
            await message.reply_text(
                "Usage: /set_ads {ads name}#{d<days> or i<impressions>}#{photo URL}\n\n"
                "Example (7 days): <code>/set_ads MyAd#d7#https://t.me/...</code>\n"
                "Example (1000 views): <code>/set_ads MyAd#i1000#https://t.me/...</code>\n\n"
                "Then reply to the ad text message."
            )
            return

        command_args = message.text.split(maxsplit=1)[1]
        if "#" not in command_args or len(command_args.split("#")) < 3:
            await message.reply_text(
                "Usage: /set_ads {ads name}#{d<days> or i<impressions>}#{photo URL}"
            )
            return

        ads_name, duration_or_impression, url = command_args.split("#", 2)
        ads_name = ads_name.strip()
        url = url.strip()

        if len(ads_name) > 35:
            await message.reply_text("Advertisement name must not exceed 35 characters.")
            return

        if not re.match(r"https?://.+", url):
            await message.reply_text("Invalid URL format. Use a valid link.")
            return

        expiry_date = None
        impression_count = None

        if duration_or_impression.startswith("d"):
            duration = duration_or_impression[1:]
            if not duration.isdigit():
                await message.reply_text("Duration must be a number (e.g. d7 for 7 days).")
                return
            expiry_date = datetime.now() + timedelta(days=int(duration))
        elif duration_or_impression.startswith("i"):
            impression = duration_or_impression[1:]
            if not impression.isdigit():
                await message.reply_text("Impression count must be a number (e.g. i1000).")
                return
            impression_count = int(impression)
        else:
            await message.reply_text(
                "Invalid prefix. Use 'd' for days (e.g. d7) or 'i' for impressions (e.g. i1000)."
            )
            return

        reply = message.reply_to_message
        if not reply:
            await message.reply_text("Reply to a text message to set it as the ad body.")
            return
        if not reply.text:
            await message.reply_text("Only text messages are supported as ad body.")
            return

        await mdb.update_advirtisment(reply.text, ads_name, expiry_date, impression_count)
        await db.set_ads_link(url)

        await asyncio.sleep(1)
        _, name, _ = await mdb.get_advirtisment()
        await message.reply_text(
            f"✅ Advertisement <b>{name}</b> has been set!\n"
            f"Photo/link: <code>{url}</code>"
        )
    except Exception as e:
        await message.reply_text(f"An error occurred: {e}")


@Client.on_message(filters.private & filters.command("ads") & filters.user(ADMINS))
async def check_ads(_, message):
    try:
        _, name, impression = await mdb.get_advirtisment()
        if not name:
            await message.reply_text("No active advertisement.")
            return
        if impression == 0:
            await message.reply_text(f"Advertisement <b>{name}</b> has expired (0 impressions left).")
            return
        status = f"{impression} impressions left" if impression is not None else "time-based (no impression limit)"
        await message.reply_text(f"📢 Active ad: <b>{name}</b>\nStatus: {status}")
    except Exception as e:
        await message.reply_text(f"An error occurred: {e}")


@Client.on_message(filters.private & filters.command("del_ads") & filters.user(ADMINS))
async def del_ads(client, message):
    try:
        await mdb.update_advirtisment()
        current_link = await db.get_ads_link()
        if current_link:
            deleted = await db.del_ads_link()
            if deleted:
                await message.reply_text(f"✅ Advertisement and photo link deleted.")
            else:
                await message.reply_text("Advertisement reset but photo link deletion failed.")
        else:
            await message.reply_text("✅ Advertisement reset. No photo link found.")
    except Exception as e:
        await message.reply_text(f"An error occurred: {e}")

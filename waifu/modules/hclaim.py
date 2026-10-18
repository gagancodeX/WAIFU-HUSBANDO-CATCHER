"""Daily character claim command: one non-store-only character per 24 hours."""
import random
import time
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackContext, CommandHandler

from waifu import application, collection, user_collection

COOLDOWN = 86400


async def hclaim(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    await user_collection.update_one(
        {"id": user.id},
        {"$setOnInsert": {
            "username": user.username, "first_name": user.first_name,
            "characters": [], "coins": 0, "xp": 0, "wins": 0,
            "total_guesses": 0, "favorites": [],
        }},
        upsert=True,
    )
    doc = await user_collection.find_one({"id": user.id}) or {}
    now = time.time()
    last = float(doc.get("last_hclaim", 0) or 0)
    if now - last < COOLDOWN:
        remaining = max(1, int(COOLDOWN - (now - last)))
        hours, remainder = divmod(remaining, 3600)
        await message.reply_text(
            f"⏳ Daily character already claimed. Try again in "
            f"<b>{hours}h {remainder // 60}m</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    owned = [char.get("id") for char in doc.get("characters", []) if char.get("id") is not None]
    choices = await collection.find(
        {"store_only": {"$ne": True}, "id": {"$nin": owned}}
    ).to_list(length=5000)
    if not choices:
        await message.reply_text("🎴 No eligible new characters are available. Store-only characters are excluded.")
        return

    char = random.choice(choices)
    result = await user_collection.update_one(
        {
            "id": user.id,
            "$and": [
                {"$or": [
                    {"last_hclaim": {"$lte": now - COOLDOWN}},
                    {"last_hclaim": {"$exists": False}},
                ]},
                {"characters.id": {"$ne": char["id"]}},
            ],
        },
        {"$push": {"characters": char}, "$set": {
            "last_hclaim": now, "username": user.username, "first_name": user.first_name,
        }},
    )
    if result.modified_count != 1:
        await message.reply_text("⏳ Claim state changed. Check /harem before trying again.")
        return

    await message.reply_text(
        f"🎁 <b>Daily character claimed!</b>\n\n"
        f"🌸 <b>{escape(str(char.get('name', 'Unknown')))}</b>\n"
        f"📺 {escape(str(char.get('anime', 'Unknown')))}\n"
        f"💎 {escape(str(char.get('rarity', '🎴')))}\n"
        f"🆔 <code>{escape(str(char.get('id', '?')))}</code>\n\n"
        "Come back in 24 hours for another claim!",
        parse_mode=ParseMode.HTML,
    )


application.add_handler(CommandHandler("hclaim", hclaim, block=False))

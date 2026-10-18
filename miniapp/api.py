"""Telegram Mini App API. Deploy separately on Render with uvicorn miniapp.api:app --host 0.0.0.0 --port $PORT."""
import hashlib, hmac, json, os, time
from pathlib import Path
from urllib.parse import parse_qsl
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
MONGO_URI = os.environ.get("MONGO_URI", "").strip()
DB_NAME = os.environ.get("DB_NAME", "waifu_bot")
OWNER_ID = int(os.environ.get("OWNER_ID", "0") or 0)
if not TOKEN or not MONGO_URI:
    raise RuntimeError("Set BOT_TOKEN and MONGO_URI in the Mini App service environment.")
db = AsyncIOMotorClient(MONGO_URI)[DB_NAME]
users, chars = db["users"], db["anime_characters"]
admins, settings = db["miniapp_admins"], db["miniapp_settings"]
app = FastAPI(title="Waifu Catcher Mini App")
INDEX = Path(__file__).with_name("index.html")


class BuyBody(BaseModel):
    character_id: str = Field(min_length=1, max_length=64)


class AdminBody(BaseModel):
    user_id: int
    permissions: list[str] = []


class CharacterPatch(BaseModel):
    store_only: bool | None = None
    store_enabled: bool | None = None
    store_price: int | None = Field(default=None, ge=0, le=1_000_000_000)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    anime: str | None = Field(default=None, min_length=1, max_length=120)
    rarity: str | None = Field(default=None, min_length=1, max_length=80)


class SettingsPatch(BaseModel):
    daily_coins: int | None = Field(default=None, ge=0, le=1_000_000)
    hclaim_enabled: bool | None = None
    shop_enabled: bool | None = None


def verify(raw: str) -> dict:
    try:
        values = dict(parse_qsl(raw, keep_blank_values=True, strict_parsing=True))
        received = values.pop("hash")
        auth_date = int(values["auth_date"])
        tg_user = json.loads(values["user"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise HTTPException(401, "Invalid Telegram authentication data.")
    if time.time() - auth_date > 86400 or auth_date - time.time() > 60:
        raise HTTPException(401, "Telegram authentication data expired.")
    data = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received) or not isinstance(tg_user.get("id"), int):
        raise HTTPException(401, "Telegram authentication could not be verified.")
    return tg_user


async def auth(header: str | None):
    if not header or not header.startswith("tma "):
        raise HTTPException(401, "Open the app from Telegram.")
    tg = verify(header[4:].strip())
    await users.update_one({"id": tg["id"]}, {"$setOnInsert": {
        "id": tg["id"], "username": tg.get("username"), "first_name": tg.get("first_name", "Player"),
        "characters": [], "coins": 0, "xp": 0, "wins": 0, "total_guesses": 0, "favorites": [],
    }}, upsert=True)
    doc = await users.find_one({"id": tg["id"]}) or {}
    if tg["id"] == OWNER_ID and OWNER_ID:
        perms = {"*"}
    else:
        admin = await admins.find_one({"user_id": tg["id"], "enabled": {"$ne": False}}) or {}
        perms = set(admin.get("permissions", []))
    return tg, doc, perms


def require(perms: set[str], name: str):
    if "*" not in perms and name not in perms:
        raise HTTPException(403, "Permission denied.")


def public(c: dict) -> dict:
    return {k: c.get(k, d) for k, d in {
        "id": "", "name": "Unknown", "anime": "Unknown", "rarity": "🎴",
        "img_url": "", "store_only": False, "store_enabled": True, "store_price": 0,
    }.items()}


@app.get("/")
async def index():
    return FileResponse(INDEX)


@app.get("/api/me")
async def me(authorization: str | None = Header(default=None)):
    tg, d, p = await auth(authorization)
    return {"user": {"id": tg["id"], "name": tg.get("first_name", "Player")},
            "coins": d.get("coins", 0), "xp": d.get("xp", 0), "wins": d.get("wins", 0),
            "collection": [public(c) for c in d.get("characters", [])],
            "permissions": sorted(p), "is_owner": "*" in p}


@app.get("/api/catalogue")
async def catalogue(authorization: str | None = Header(default=None), q: str = ""):
    await auth(authorization)
    query = {"store_only": {"$ne": True}}
    if q.strip():
        import re
        pattern = re.escape(q.strip()[:80])
        query["$or"] = [{"name": {"$regex": pattern, "$options": "i"}},
                        {"anime": {"$regex": pattern, "$options": "i"}}]
    result = await chars.find(query).limit(100).to_list(100)
    return {"characters": [public(c) for c in result]}


@app.get("/api/shop")
async def shop(authorization: str | None = Header(default=None)):
    await auth(authorization)
    cfg = await settings.find_one({"_id": "global"}) or {}
    if cfg.get("shop_enabled") is False:
        return {"items": []}
    result = await chars.find({"store_only": True, "store_enabled": {"$ne": False}}).limit(100).to_list(100)
    return {"items": [public(c) for c in result]}


@app.post("/api/shop/buy")
async def buy(body: BuyBody, authorization: str | None = Header(default=None)):
    tg, d, _ = await auth(authorization)
    cfg = await settings.find_one({"_id": "global"}) or {}
    if cfg.get("shop_enabled") is False:
        raise HTTPException(403, "Shop is disabled.")
    c = await chars.find_one({"id": body.character_id, "store_only": True, "store_enabled": {"$ne": False}})
    if not c:
        raise HTTPException(404, "Character is not available in the store.")
    price = max(0, int(c.get("store_price", 0) or 0))
    result = await users.update_one(
        {"id": tg["id"], "coins": {"$gte": price}, "characters.id": {"$ne": body.character_id}},
        {"$inc": {"coins": -price}, "$push": {"characters": c}},
    )
    if result.modified_count != 1:
        raise HTTPException(409, "Not enough coins, or you already own this character. Refresh and retry.")
    return {"ok": True, "character": public(c), "coins": int(d.get("coins", 0)) - price}


@app.post("/api/daily")
async def daily(authorization: str | None = Header(default=None)):
    tg, _, _ = await auth(authorization)
    now = time.time()
    cfg = await settings.find_one({"_id": "global"}) or {}
    reward = int(cfg.get("daily_coins", 200))
    result = await users.update_one({"id": tg["id"], "$or": [
        {"last_daily": {"$lte": now - 86400}}, {"last_daily": {"$exists": False}},
    ]}, {"$inc": {"coins": reward}, "$set": {"last_daily": now}})
    if result.modified_count != 1:
        raise HTTPException(409, "Daily coins already claimed.")
    return {"ok": True, "coins_awarded": reward}


@app.post("/api/hclaim")
async def hclaim(authorization: str | None = Header(default=None)):
    tg, d, _ = await auth(authorization)
    now = time.time()
    cfg = await settings.find_one({"_id": "global"}) or {}
    if cfg.get("hclaim_enabled") is False:
        raise HTTPException(403, "Daily character claim is disabled.")
    owned = [c.get("id") for c in d.get("characters", []) if c.get("id") is not None]
    choices = await chars.find({"store_only": {"$ne": True}, "id": {"$nin": owned}}).limit(5000).to_list(5000)
    if not choices:
        raise HTTPException(404, "No eligible new characters are available.")
    import random
    c = random.choice(choices)
    result = await users.update_one({"id": tg["id"], "$and": [
        {"$or": [{"last_hclaim": {"$lte": now - 86400}}, {"last_hclaim": {"$exists": False}}]},
        {"characters.id": {"$ne": c["id"]}},
    ]}, {"$push": {"characters": c}, "$set": {"last_hclaim": now}})
    if result.modified_count != 1:
        raise HTTPException(409, "Daily character already claimed or collection changed.")
    return {"ok": True, "character": public(c)}


@app.get("/api/admin/characters")
async def admin_characters(authorization: str | None = Header(default=None)):
    tg, _, perms = await auth(authorization)
    require(perms, "manage_characters")
    docs = await chars.find({}).limit(500).to_list(500)
    return {"characters": [public(c) for c in docs]}


@app.patch("/api/admin/characters/{char_id}")
async def edit_character(char_id: str, body: CharacterPatch, authorization: str | None = Header(default=None)):
    tg, _, perms = await auth(authorization)
    require(perms, "manage_characters")
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(400, "No changes supplied.")
    result = await chars.update_one({"id": char_id}, {"$set": changes})
    if result.matched_count != 1:
        raise HTTPException(404, "Character not found.")
    return {"ok": True}


@app.get("/api/admin/settings")
async def get_settings(authorization: str | None = Header(default=None)):
    tg, _, perms = await auth(authorization)
    require(perms, "manage_settings")
    d = await settings.find_one({"_id": "global"}) or {}
    return {"daily_coins": d.get("daily_coins", 200), "shop_enabled": d.get("shop_enabled", True),
            "hclaim_enabled": d.get("hclaim_enabled", True)}


@app.patch("/api/admin/settings")
async def set_settings(body: SettingsPatch, authorization: str | None = Header(default=None)):
    tg, _, perms = await auth(authorization)
    require(perms, "manage_settings")
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(400, "No changes supplied.")
    await settings.update_one({"_id": "global"}, {"$set": changes}, upsert=True)
    return {"ok": True}


@app.put("/api/admin/admins")
async def set_admin(body: AdminBody, authorization: str | None = Header(default=None)):
    tg, _, perms = await auth(authorization)
    if tg["id"] != OWNER_ID or not OWNER_ID:
        raise HTTPException(403, "Only the owner can manage admin roles.")
    if body.user_id == OWNER_ID:
        raise HTTPException(400, "Owner permissions cannot be changed.")
    allowed = {"manage_characters", "manage_settings", "manage_admins", "manage_events", "manage_shop", "view_analytics"}
    if not set(body.permissions).issubset(allowed):
        raise HTTPException(400, "Unsupported permission.")
    await admins.update_one({"user_id": body.user_id}, {"$set": {
        "user_id": body.user_id, "permissions": body.permissions, "enabled": True,
        "updated_by": tg["id"], "updated_at": int(time.time()),
    }}, upsert=True)
    return {"ok": True}

@app.delete("/api/admin/admins/{user_id}")
async def delete_admin(user_id: int, authorization: str | None = Header(default=None)):
    tg, _, _ = await auth(authorization)
    if tg["id"] != OWNER_ID or not OWNER_ID:
        raise HTTPException(403, "Only the owner can manage admin roles.")
    if user_id == OWNER_ID:
        raise HTTPException(400, "The owner cannot be removed.")
    await admins.delete_one({"user_id": user_id})
    return {"ok": True}


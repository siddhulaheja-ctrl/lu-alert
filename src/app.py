"""Lambda entry points. api: HTTP routes (Telegram/Vonage webhooks + dashboard). daily: 6 AM IST alert run."""
import base64
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from decimal import Decimal

import boto3
from botocore.config import Config

from flow import handle, parse_telegram, parse_vonage, today_alert
from heat import daily_risk, fetch_hourly
from messages import render

table = boto3.resource("dynamodb").Table(os.environ["TABLE"])
s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
polly = boto3.client("polly")
lam = boto3.client("lambda")
BUCKET = os.environ["BUCKET"]
VOICE_LANG = {"hi": "hi-IN", "en": "en-IN"}  # Kajal (neural) speaks both


# ---------- storage ----------

def load(phone):
    item = table.get_item(Key={"phone": phone}).get("Item")
    return item and {k: float(v) if isinstance(v, Decimal) else v for k, v in item.items()}


def save(user):
    table.put_item(Item=json.loads(json.dumps(user), parse_float=Decimal))


def signed_up():
    pages = table.meta.client.get_paginator("scan").paginate(TableName=table.name)
    for item in (i for p in pages for i in p["Items"]):
        if item.get("step") == "done":
            yield {k: float(v) if isinstance(v, Decimal) else v for k, v in item.items()}


# ---------- sending ----------

def post(url, payload, headers=None):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


LOCATION_BUTTON = {"hi": "📍 मेरी लोकेशन भेजें", "en": "📍 Send my location"}


def tg_secret():
    """Webhook secret derived from the bot token, so there is nothing extra to configure."""
    return hashlib.sha256(("lu-alert:" + os.environ.get("TELEGRAM_BOT_TOKEN", "")).encode()).hexdigest()


def telegram(user, text=None, audio=None):
    """Text (WhatsApp-style *bold* via Markdown) or voice note to a Telegram chat."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = user["phone"].removeprefix("tg:")
    if not token:
        print("DRY_RUN telegram", chat, json.dumps(text or audio, ensure_ascii=False))
        return
    api = f"https://api.telegram.org/bot{token}/"
    if not text:
        post(api + "sendVoice", {"chat_id": chat, "voice": audio})
        return
    keyboard = ({"keyboard": [[{"text": LOCATION_BUTTON[user.get("lang", "hi")], "request_location": True}]],
                 "resize_keyboard": True, "one_time_keyboard": True}
                if user.get("step") == "place" else {"remove_keyboard": True})
    msg = {"chat_id": chat, "text": text, "parse_mode": "Markdown", "reply_markup": keyboard}
    try:
        post(api + "sendMessage", msg)
    except urllib.error.HTTPError as ex:  # stray Markdown char (e.g. "_" in a place name): resend as plain text
        if ex.code != 400:
            raise
        post(api + "sendMessage", {k: v for k, v in msg.items() if k != "parse_mode"})
    print("sent telegram", chat[-4:])


def send(user, text=None, audio=None):
    """One message (text, or audio URL) on the worker's channel: Telegram, else Vonage WhatsApp. No creds = dry run."""
    if user.get("channel") == "telegram":
        return telegram(user, text, audio)
    e = os.environ
    body = {"message_type": "text", "text": text} if text else {"message_type": "audio", "audio": {"url": audio}}
    data = {"from": e["VONAGE_WA_FROM"], "to": user["phone"], "channel": "whatsapp", **body}
    creds = (e.get("VONAGE_API_KEY"), e.get("VONAGE_API_SECRET"))
    if not all(creds):
        print("DRY_RUN send", user["phone"], json.dumps(text or audio, ensure_ascii=False))
        return
    auth = base64.b64encode(":".join(creds).encode()).decode()
    print("sent vonage", user["phone"][-4:], post(e["VONAGE_MESSAGES_URL"], data, {"Authorization": f"Basic {auth}"}))


def voice_url(text, lang, _cache={}):
    """Polly mp3 -> S3 -> presigned URL. Same text (many workers, same city) is synthesized once per container."""
    key = f"voice/{hashlib.sha1(f'{lang}:{text}'.encode()).hexdigest()}.mp3"
    if key not in _cache:
        audio = polly.synthesize_speech(
            Engine="neural", VoiceId="Kajal", LanguageCode=VOICE_LANG[lang], OutputFormat="mp3", Text=text
        )["AudioStream"].read()
        s3.put_object(Bucket=BUCKET, Key=key, Body=audio, ContentType="audio/mpeg")
        _cache[key] = s3.generate_presigned_url("get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=6 * 3600)
    return _cache[key]


def send_alert(user, text, voice):
    send(user, text=text)
    if voice:
        send(user, audio=voice_url(voice, user["lang"]))


def send_all(day=None):
    """Alert every signed-up worker whose day is not green. Returns count sent."""
    sent = 0
    for user in signed_up():
        try:
            msg = render(daily_risk(fetch_hourly(user["lat"], user["lon"], day), user["work"]), user["lang"])
            if msg:
                send_alert(user, *msg)
                sent += 1
        except Exception as ex:  # one bad number/outage must not stop everyone else's alert
            print("alert failed", user["phone"][-4:], repr(ex))
    print("alerts sent", sent, "day", day or "today")
    return sent


# ---------- webhook auth ----------

def b64url(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def vonage_signed(headers, body):
    """Verify Vonage's HS256 JWT (Authorization: Bearer) incl. payload_hash. Skipped until the secret is set."""
    secret = os.environ.get("VONAGE_SIGNATURE_SECRET")
    if not secret:
        return True  # ponytail: open webhook until signature secret is configured; set it before the demo
    try:
        h, p, sig = headers.get("authorization", "").split(" ")[-1].split(".")
        good = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
        claims = json.loads(b64url(p))
        return hmac.compare_digest(good, b64url(sig)) and claims.get("payload_hash") == hashlib.sha256(body.encode()).hexdigest()
    except ValueError:
        return False


def is_admin(headers):
    return hmac.compare_digest(headers.get("x-admin-key", ""), os.environ["ADMIN_KEY"])


# ---------- handlers ----------

def converse(phone, name, msg, channel):
    user, replies = handle(load(phone), msg, phone=phone, name=name)
    user["channel"] = channel
    save(user)
    for r in replies:
        if r is None:  # flow asks for today's alert (after signup or "aaj")
            send_alert(user, *today_alert(user))
        else:
            send(user, text=r)


def reply(status, obj=None):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(obj or {}, ensure_ascii=False)}


def api(event, _ctx):
    route = event["routeKey"]
    headers = event.get("headers") or {}
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    day = (event.get("queryStringParameters") or {}).get("date") or None

    if route == "POST /inbound":
        print("inbound signed:", headers.get("authorization", "").startswith("Bearer "))
        if not vonage_signed(headers, body):
            return reply(401)
        payload = json.loads(body)
        if payload.get("channel") != "whatsapp" or "from" not in payload:
            return reply(200)
        converse(*parse_vonage(payload), "vonage")
        return reply(200)

    if route == "POST /telegram":
        if not os.environ.get("TELEGRAM_BOT_TOKEN") or not hmac.compare_digest(
                headers.get("x-telegram-bot-api-secret-token", ""), tg_secret()):
            return reply(401)
        parsed = parse_telegram(json.loads(body))
        try:
            if parsed:
                converse(*parsed, "telegram")
        except Exception as ex:  # Telegram re-sends on non-200, which would repeat replies; log and ack instead
            print("[ERROR] telegram converse failed", repr(ex))
        return reply(200)

    if route == "POST /status":  # delivery receipts; logged for debugging
        print("status", body[:500])
        return reply(200)

    if not is_admin(headers):
        return reply(401, {"error": "admin key required"})

    if route == "GET /workers":
        cache, rows = {}, []
        for u in signed_up():
            spot = (round(u["lat"], 2), round(u["lon"], 2))
            if spot not in cache:
                cache[spot] = fetch_hourly(*spot, day)
            rows.append({
                "name": u.get("name") or "Worker", "place": u.get("place", ""),
                "phone": ("Telegram ··" if u["phone"].startswith("tg:") else "91xxxxxx") + u["phone"][-4:],
                "lat": u["lat"], "lon": u["lon"], "work": u["work"], "lang": u["lang"], **daily_risk(cache[spot], u["work"]),
            })
        return reply(200, rows)

    if route == "POST /broadcast":  # can exceed API Gateway's 30 s, so hand off to the daily function
        lam.invoke(FunctionName=os.environ["DAILY_FN"], InvocationType="Event", Payload=json.dumps({"date": day}))
        return reply(202, {"queued": True})

    return reply(404)


def daily(event, _ctx):
    return {"sent": send_all((event or {}).get("date"))}

"""WhatsApp conversation: signup state machine + commands. Pure except geocode()/today_alert() (network).

user = {"phone", "name", "step": lang|place|confirm|work|done|stopped, "lang", "lat", "lon", "place", "work"}
msg  = {"type": "text"|"location", "text", "lat", "lon"}
"""
import json
import urllib.parse
import urllib.request

from heat import daily_risk, fetch_hourly
from messages import render

T = {
    "hi": {
        "place": "अपने काम की जगह बताएँ: 📎 दबाकर *Location* भेजें, या अपने शहर का नाम अंग्रेज़ी में लिखें (जैसे: Noida)।",
        "not_found": "यह जगह नहीं मिली। शहर का नाम अंग्रेज़ी में लिखें (जैसे: Gurugram) या लोकेशन भेजें।",
        "confirm": "क्या आपकी जगह *{place}* है?\n1 - हाँ\n2 - नहीं",
        "work": "आपका काम कितना भारी है?\n1 - हल्का (दुकान, गार्ड, ठेला)\n2 - मध्यम (डिलीवरी, रिक्शा, खेत)\n3 - भारी (मज़दूरी, निर्माण, ईंट-भट्ठा)",
        "done": "✅ आप लू अलर्ट से जुड़ गए! ज़्यादा गर्मी वाले दिन सुबह 6 बजे आपको संदेश और आवाज़ वाला अलर्ट मिलेगा।",
        "help": "लिखें:\n*आज* - आज की गर्मी\n*जगह* - जगह बदलें\n*बंद* - अलर्ट बंद करें\nतबीयत खराब हो तो लक्षण लिखें, या 108 पर कॉल करें।",
        "stopped": "अलर्ट बंद कर दिए गए। दोबारा शुरू करने के लिए *start* लिखें।",
        "green": "🟢 आज गर्मी सामान्य है। पानी पीते रहें।",
        "emergency": "🚨 यह लू (हीट स्ट्रोक) हो सकता है। अभी:\n1. तुरंत छाँव या ठंडी जगह जाएँ।\n2. कपड़े ढीले करें, शरीर पर पानी डालें या गीला कपड़ा रखें।\n3. होश में हों तो घूँट-घूँट पानी या ORS पिएँ।\n4. *108 पर कॉल करें।* बेहोश व्यक्ति को पानी न पिलाएँ।",
    },
    "en": {
        "place": "Where do you work? Tap 📎 and send your *Location*, or type your city name (e.g. Noida).",
        "not_found": "Couldn't find that place. Type your city name (e.g. Gurugram) or send your location.",
        "confirm": "Is your place *{place}*?\n1 - Yes\n2 - No",
        "work": "How heavy is your work?\n1 - Light (shop, guard, vendor)\n2 - Moderate (delivery, rickshaw, farm)\n3 - Heavy (labour, construction, brick kiln)",
        "done": "✅ You're signed up for Lu Alert! On hot days you'll get a text and voice alert at 6 AM.",
        "help": "Type:\n*today* - today's heat\n*place* - change place\n*stop* - stop alerts\nIf you feel unwell, describe it, or call 108.",
        "stopped": "Alerts stopped. Type *start* to begin again.",
        "green": "🟢 Normal heat today. Keep drinking water.",
        "emergency": "🚨 This may be heat stroke. Right now:\n1. Move to shade or a cool place.\n2. Loosen clothes, pour water on the body or use a wet cloth.\n3. If conscious, sip water or ORS.\n4. *Call 108.* Do not give water to someone who is unconscious.",
    },
}
LANG_PROMPT = "नमस्ते! लू अलर्ट में आपका स्वागत है। 🌞\nWelcome to Lu Alert!\n\n1 - हिंदी\n2 - English"
WORK = {"1": "light", "2": "moderate", "3": "heavy"}

# Fixed keyword match, so heat-stroke help never depends on an LLM. Hindi, Hinglish, English.
DANGER = [
    "चक्कर", "उल्टी", "बेहोश", "सिरदर्द", "सिर दर्द", "घबराहट", "सांस", "साँस", "लू लग",
    "chakkar", "ulti", "behosh", "sir dard", "sirdard", "ghabrahat", "saans", "lu lag",
    "dizzy", "vomit", "faint", "headache", "unconscious", "collapse", "heat stroke", "heatstroke", "nausea",
]
CMD = {
    "today": ["today", "aaj", "आज"],
    "place": ["place", "jagah", "जगह", "location"],
    "stop": ["stop", "band", "बंद"],
    "start": ["start", "shuru", "शुरू"],
}


def is_danger(text):
    t = text.lower()
    return any(k in t for k in DANGER)


def parse_vonage(p):
    """Vonage Messages API inbound webhook -> (phone, name, msg)."""
    loc = p.get("location") or {}
    msg = {"type": p.get("message_type"), "text": (p.get("text") or "").strip(), "lat": loc.get("lat"), "lon": loc.get("long")}
    return p["from"], (p.get("profile") or {}).get("name", ""), msg


def parse_telegram(update):
    """Telegram update -> (phone, name, msg) with phone = 'tg:<chat id>'; None for non-message updates."""
    m = update.get("message")
    if not m:
        return None
    loc = m.get("location") or {}
    msg = {"type": "location" if loc else "text", "text": (m.get("text") or "").lstrip("/").strip(),
           "lat": loc.get("latitude"), "lon": loc.get("longitude")}
    return f"tg:{m['chat']['id']}", (m.get("from") or {}).get("first_name", ""), msg


def geocode(name):
    """Most populous Indian match -> (lat, lon, 'Name, State') or None."""
    q = urllib.parse.urlencode({"name": name, "count": 5, "countryCode": "IN", "language": "en"})
    with urllib.request.urlopen(f"https://geocoding-api.open-meteo.com/v1/search?{q}", timeout=10) as r:
        res = json.load(r).get("results") or []
    if not res:
        return None
    best = max(res, key=lambda x: x.get("population") or 0)
    return best["latitude"], best["longitude"], f"{best['name']}, {best.get('admin1', '')}".rstrip(", ")


def today_alert(user, day=None):
    """(text, voice|None) for a signed-up user; voice is None on green days. day='YYYY-MM-DD' replays."""
    msg = render(daily_risk(fetch_hourly(user["lat"], user["lon"], day), user["work"]), user["lang"])
    return msg or (T[user["lang"]]["green"], None)


def handle(user, msg, phone="", name="", geo=geocode):
    """(user, msg) -> (updated user, [replies]). A reply of None means 'send today_alert(user)'."""
    if user is None:
        return {"phone": phone, "name": name, "step": "lang"}, [LANG_PROMPT]
    text = msg.get("text", "")
    low = text.lower()
    lang = user.get("lang", "hi")
    t = T[lang]
    step = user["step"]
    say = lambda key, **kw: t[key].format(**kw)  # noqa: E731

    if text and is_danger(text):
        return user, [t["emergency"]] if "lang" in user else [T["hi"]["emergency"], T["en"]["emergency"]]

    if step == "lang":
        pick = "hi" if low in ("1", "hindi", "हिंदी") else "en" if low in ("2", "english") else None
        if not pick:
            return user, [LANG_PROMPT]
        return {**user, "lang": pick, "step": "place"}, [T[pick]["place"]]

    if step == "place":
        if msg["type"] == "location" and msg.get("lat") is not None:
            return {**user, "lat": msg["lat"], "lon": msg["lon"], "place": "📍", "step": "work"}, [say("work")]
        found = geo(text) if text else None
        if not found:
            return user, [say("not_found")]
        lat, lon, place = found
        return {**user, "lat": lat, "lon": lon, "place": place, "step": "confirm"}, [say("confirm", place=place)]

    if step == "confirm":
        if low in ("1", "haan", "ha", "yes", "हाँ", "हां"):
            return {**user, "step": "work"}, [say("work")]
        return {**user, "step": "place"}, [say("place")]

    if step == "work":
        if low not in WORK:
            return user, [say("work")]
        return {**user, "work": WORK[low], "step": "done"}, [say("done"), None]

    if step == "stopped":
        if low in CMD["start"]:
            return {**user, "step": "done"}, [say("done")]
        return user, [say("stopped")]

    # step == "done"
    if low in CMD["today"]:
        return user, [None]
    if low in CMD["place"]:
        return {**user, "step": "place"}, [say("place")]
    if low in CMD["stop"]:
        return {**user, "step": "stopped"}, [say("stopped")]
    return user, [say("help")]  # Tier 2: hand free-text questions to the Strands agent here


if __name__ == "__main__":
    fake_geo = lambda q: (28.58, 77.33, "Noida, Uttar Pradesh") if q.lower() == "noida" else None  # noqa: E731
    txt = lambda s: {"type": "text", "text": s}  # noqa: E731

    u, r = handle(None, txt("hi"), phone="919999999999", name="Ramesh")
    assert u["step"] == "lang" and "English" in r[0]
    u, r = handle(u, txt("1"))
    assert u["lang"] == "hi" and u["step"] == "place"
    u, r = handle(u, txt("xyzabc"), geo=fake_geo)
    assert u["step"] == "place" and "नहीं मिली" in r[0]
    u, r = handle(u, txt("Noida"), geo=fake_geo)
    assert u["step"] == "confirm" and "Noida, Uttar Pradesh" in r[0]
    u, r = handle(u, txt("2"))
    assert u["step"] == "place"  # wrong place -> ask again
    u, r = handle(u, {"type": "location", "lat": 28.6, "lon": 77.2})
    assert u["step"] == "work" and u["lat"] == 28.6
    u, r = handle(u, txt("5"))
    assert u["step"] == "work"
    u, r = handle(u, txt("3"))
    assert u["work"] == "heavy" and u["step"] == "done" and r[-1] is None
    assert handle(u, txt("aaj"))[1] == [None]
    assert "108" in handle(u, txt("mujhe chakkar aa raha hai"))[1][0]
    assert len(handle({"step": "lang"}, txt("I feel dizzy"))[1]) == 2  # no language yet -> both
    u, r = handle(u, txt("band"))
    assert u["step"] == "stopped"
    u, r = handle(u, txt("start"))
    assert u["step"] == "done"
    assert "*आज*" in handle(u, txt("kuch bhi"))[1][0]
    assert not is_danger("aaj bahut garmi hai")
    assert parse_telegram({"message": {"chat": {"id": 42}, "from": {"first_name": "R"}, "text": "/start"}}) == (
        "tg:42", "R", {"type": "text", "text": "start", "lat": None, "lon": None})
    assert parse_telegram({"message": {"chat": {"id": 42}, "location": {"latitude": 28.6, "longitude": 77.2}}})[2]["lat"] == 28.6
    assert parse_telegram({"edited_message": {}}) is None
    print("flow.py ok")

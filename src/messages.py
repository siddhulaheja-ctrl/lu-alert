"""Alert text for WhatsApp (with emoji) and Polly voice (plain). Safety advice is fixed text, never LLM-written."""

EMOJI = ["🟢", "🟡", "🟠", "🔴", "🔴"]

HI = {
    "head": [
        "",
        "लू अलर्ट: आज गर्मी ज़्यादा रहेगी।",
        "लू अलर्ट: आज बहुत तेज़ गर्मी रहेगी।",
        "लू अलर्ट: खतरनाक गर्मी! आज लू चलेगी।",
        "लू अलर्ट: बहुत खतरनाक गर्मी! आज जान का खतरा है।",
    ],
    "peak": "सबसे ज़्यादा गर्मी {peak} के आसपास होगी, तापमान {tmax} डिग्री तक।",
    "plan": [
        "",
        "{start} से {end} तक हर घंटे 15 मिनट छाँव में आराम करें।",
        "{start} से {end} तक हर घंटे 30 मिनट काम करें और 30 मिनट छाँव में आराम करें।",
        "{start} से {end} तक हर घंटे सिर्फ़ 15 मिनट काम करें, बाकी समय छाँव में रहें।",
        "भारी काम {start} से पहले निपटा लें। {start} से {end} तक धूप में भारी काम न करें; ज़रूरी काम हो तो हर घंटे सिर्फ़ 15 मिनट करें, बाकी समय छाँव में रहें।",
    ],
    "water": "हर 20 मिनट में एक गिलास पानी पिएँ, प्यास न लगे तब भी।",
    "ors": "पानी में ओ आर एस या नमक-चीनी मिलाकर पिएँ।",
    "signs": "चक्कर, उल्टी, तेज़ सिरदर्द या बेहोशी लगे तो तुरंत छाँव में जाएँ और 108 पर कॉल करें।",
}

EN = {
    "head": [
        "",
        "Heat alert: it will be hot today.",
        "Heat alert: it will be very hot today.",
        "Heat alert: dangerous heat today!",
        "Heat alert: extreme, life-threatening heat today!",
    ],
    "peak": "Hottest around {peak}, up to {tmax} degrees.",
    "plan": [
        "",
        "From {start} to {end}, rest 15 minutes in the shade every hour.",
        "From {start} to {end}, work 30 minutes, then rest 30 minutes in the shade, every hour.",
        "From {start} to {end}, work only 15 minutes per hour and stay in the shade the rest.",
        "Finish heavy work before {start}. From {start} to {end}, avoid heavy work in the sun; if you must work, do only 15 minutes per hour and rest in the shade the rest.",
    ],
    "water": "Drink a glass of water every 20 minutes, even if you are not thirsty.",
    "ors": "Add ORS, or a pinch of salt and sugar, to your water.",
    "signs": "If you feel dizzy, vomit, get a bad headache or feel faint, move to shade and call 108.",
}


def hi_time(h):
    part = "सुबह" if h < 12 else "दोपहर" if h < 16 else "शाम" if h < 20 else "रात"
    return f"{part} {h % 12 or 12} बजे"


def en_time(h):
    return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"


def render(risk, lang="hi"):
    """(whatsapp_text, voice_text) for a daily_risk() result, or None when it's a green day (stay quiet)."""
    lv = risk["level"]
    if lv == 0:
        return None
    t, fmt = (HI, hi_time) if lang == "hi" else (EN, en_time)
    start, end = (fmt(h) for h in risk["window"])
    lines = [
        t["head"][lv],
        t["plan"][lv].format(start=start, end=end),
        t["peak"].format(peak=fmt(risk["peak_hour"]), tmax=risk["tmax"]),
        t["water"],
    ]
    if lv >= 2:
        lines += [t["ors"], t["signs"]]
    voice = " ".join(lines)
    return f"{EMOJI[lv]} " + "\n".join(lines), voice


if __name__ == "__main__":
    risk = {"level": 4, "window": (12, 17), "peak_hour": 15, "tmax": 46}
    text, voice = render(risk, "hi")
    assert "दोपहर 12 बजे" in text and "शाम 5 बजे" in text and "108" in voice and "🔴" not in voice
    assert "12 PM" in render(risk, "en")[0]
    assert render({**risk, "level": 0}) is None
    assert "108" not in render({**risk, "level": 1}, "en")[0]  # mild days stay short
    print("messages.py ok")

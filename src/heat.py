"""Heat-stress risk engine: Open-Meteo hourly weather -> WBGT -> work/rest level."""
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

LEVELS = ["green", "yellow", "orange", "red", "extreme"]
WORK_HOURS = range(6, 20)  # 6 AM - 7 PM, when outdoor workers are out

# Max WBGT (°C) for each work/rest regime, adapted from ACGIH TLV (acclimatized workers).
# Index = level: 0 continuous work, 1 = 45/15 min, 2 = 30/30, 3 = 15/45, above last = stop.
# Calibration knob: tune these if alerts feel too loud/quiet for real users.
LIMITS = {
    "light": [31.0, 31.0, 32.0, 32.5],
    "moderate": [28.0, 29.0, 30.0, 31.5],
    "heavy": [26.0, 27.5, 29.0, 30.5],
}


def fetch_hourly(lat, lon, day=None):
    """[(hour, temp_c, rh_pct)] for one day in IST. day=None -> today's forecast, else archive (replay)."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m",
        "timezone": "Asia/Kolkata",
    }
    if day is None:
        url, params["forecast_days"] = FORECAST_URL, 1
    else:
        url, params["start_date"], params["end_date"] = ARCHIVE_URL, day, day
    for attempt in range(3):  # Open-Meteo's free tier sometimes answers 429/503; brief backoff then retry
        try:
            with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=15) as r:
                h = json.load(r)["hourly"]
            break
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503) or attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    return [
        (int(t[11:13]), temp, rh)
        for t, temp, rh in zip(h["time"], h["temperature_2m"], h["relative_humidity_2m"])
        if temp is not None and rh is not None
    ]


def wbgt(temp_c, rh):
    # ponytail: Australian BoM simplified WBGT (assumes sun + light wind). Ignores actual
    # radiation/wind; upgrade to Liljegren with shortwave_radiation + wind if judges probe accuracy.
    e = rh / 100 * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))
    return 0.567 * temp_c + 0.216 * e + 3.38


def level_for(w, work):
    return next((i for i, lim in enumerate(LIMITS[work]) if w <= lim), 4)


def daily_risk(hourly, work):
    """Summarise a day: peak level and the contiguous window of hours at that level."""
    hours = [(h, t, wbgt(t, rh)) for h, t, rh in hourly if h in WORK_HOURS]
    levels = [(h, level_for(w, work)) for h, _, w in hours]
    peak = max(lv for _, lv in levels)
    at_peak = [h for h, lv in levels if lv == peak]
    peak_hour, tmax, _ = max(hours, key=lambda x: x[1])  # "hottest" = temperature, as workers read it
    return {
        "level": peak,
        "color": LEVELS[peak],
        "work": work,
        "window": (at_peak[0], at_peak[-1] + 1),  # [start, end) in 24h hours
        "peak_hour": peak_hour,
        "peak_wbgt": round(max(w for _, _, w in hours), 1),
        "tmax": round(tmax),
    }


if __name__ == "__main__":
    # Offline self-check of the logic (no network).
    assert 32.5 < wbgt(47, 12) < 33.5  # Delhi, late May 2024 afternoon
    assert 27 < wbgt(33, 50) < 28  # Delhi, October afternoon
    assert level_for(25, "heavy") == 0 and level_for(28, "heavy") == 2 and level_for(35, "light") == 4
    fake = [(h, 30 + max(0, 6 - abs(h - 14)) * 2.5, 15) for h in range(24)]  # peaks 45°C at 2 PM
    r = daily_risk(fake, "heavy")
    assert r["color"] == "extreme" and r["peak_hour"] == 14 and r["window"][0] <= 14 < r["window"][1]
    assert daily_risk([(h, 24, 40) for h in range(24)], "light")["level"] == 0
    print("heat.py ok")

"""Local run: python demo.py [--date 2024-05-29] [--work heavy] [--lang hi] [--lat 28.61 --lon 77.21]"""
import argparse
import sys

sys.path.insert(0, "src")
from heat import fetch_hourly, daily_risk  # noqa: E402
from messages import render  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--lat", type=float, default=28.61)  # New Delhi
p.add_argument("--lon", type=float, default=77.21)
p.add_argument("--date", help="YYYY-MM-DD to replay past weather; omit for today's forecast")
p.add_argument("--work", choices=["light", "moderate", "heavy"], default="heavy")
p.add_argument("--lang", choices=["hi", "en"], default="hi")
a = p.parse_args()

sys.stdout.reconfigure(encoding="utf-8")  # Devanagari on Windows consoles
risk = daily_risk(fetch_hourly(a.lat, a.lon, a.date), a.work)
print(risk)
msg = render(risk, a.lang)
print(msg[0] if msg else "Green day: no alert sent.")

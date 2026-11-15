"""Print a random POST /reset body: on each weekday in the next 6 days (UTC), 1-3 doctors with 2-3 slots each."""

import json
import random
from collections import Counter
from datetime import UTC, datetime, time, timedelta

PROVIDERS = ["Dr Ade", "Dr Bello", "Dr Chukwu"]
STARTS = [time(h, m) for h in range(9, 17) for m in (0, 30)]  # 09:00 to 16:30, 30-minute slots

today = datetime.now(UTC).date()
days = [d for d in (today + timedelta(n) for n in range(1, 7)) if d.weekday() < 5]

starts = []
for day in days:
    for provider in random.sample(PROVIDERS, random.randint(1, 3)):
        starts += [(datetime.combine(day, t, UTC), provider) for t in random.sample(STARTS, random.randint(2, 3))]

slots = [
    {
        "id": f"demo-{i:02d}",
        "provider": provider,
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": (start + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
    }
    for i, (start, provider) in enumerate(sorted(starts), 1)
]

per_doctor_day = Counter((s["start"][:10], s["provider"]) for s in slots)
assert all(2 <= n <= 3 for n in per_doctor_day.values())
assert all(1 <= n <= 3 for n in Counter(d for d, _ in per_doctor_day).values())
assert all(today < datetime.fromisoformat(s["end"]).date() <= today + timedelta(6) for s in slots)
print(json.dumps({"slots": slots}, indent=2))

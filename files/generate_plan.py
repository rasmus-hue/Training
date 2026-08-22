#!/usr/bin/env python3
"""One-off generator for the half-marathon + Zwift + PPL training plan.

Builds two things and upserts them straight to Supabase:
  - plan_weekly:  44 weeks of periodized targets, today -> race week
  - plan_daily:   day-by-day sessions for the next 7 days only

Re-run this (with fresh week_start / today values) whenever the plan needs
regenerating -- e.g. "update my dashboard" re-runs the plan_daily half for
the next 7 days based on the current week's targets.
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request
from datetime import date, timedelta

# Fixed anchor for week numbering/phases -- do NOT change on re-runs, or every
# week's phase and week_start would shift and orphan the old plan_weekly rows.
PLAN_START_MONDAY = date(2026, 8, 17)
TODAY = date.today()
RACE_DAY = date(2027, 6, 20)
RACE_GOAL = "1:50-1:55 halvmaraton"
RACE_PACE = "5:15-5:25/km"

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

WEEKDAY_TEMPLATE = {
    # 0=Mon .. 6=Sun
    0: ["bike_endurance", "strength_push"],
    1: ["run_easy", "strength_pull"],
    2: ["bike_quality"],
    3: ["run_quality", "strength_push"],
    4: ["bike_endurance", "strength_pull"],
    5: ["run_long"],
    6: ["bike_recovery", "strength_legs"],
}

STEPS_GOAL = 12000


def phase_for_week(w: int) -> str:
    if w <= 14:
        return "base"
    if w <= 26:
        return "build1"
    if w <= 36:
        return "build2"
    if w <= 41:
        return "peak"
    if w <= 43:
        return "taper"
    return "race"


def is_cutback(w: int, phase: str) -> bool:
    return phase in ("base", "build1", "build2", "peak") and w % 4 == 0


def lerp(a, b, frac):
    return a + (b - a) * max(0.0, min(1.0, frac))


def long_run_km(w: int, phase: str) -> float:
    if phase == "race":
        return 21.1
    if phase == "taper":
        return {42: 12.0, 43: 8.0}[w]
    if phase == "base":
        val = lerp(5.0, 10.0, (w - 1) / 13)
    elif phase == "build1":
        val = lerp(10.0, 14.0, (w - 15) / 11)
    elif phase == "build2":
        val = lerp(14.0, 16.5, (w - 27) / 9)
    else:  # peak
        val = {37: 16.5, 38: 17.5, 39: 18.0, 40: 18.0, 41: 15.0}[w]
    if is_cutback(w, phase):
        val *= 0.75
    return round(val, 1)


def easy_run_km(long_km: float, phase: str) -> float:
    if phase == "race":
        return 3.0  # shakeout, not used race week itself
    return round(max(3.0, long_km * 0.5), 1)


def quality_run_km(long_km: float, phase: str) -> float:
    if phase == "race":
        return 3.0
    return round(max(3.0, long_km * 0.55), 1)


def easy_pace(phase: str) -> str:
    return {"base": "6:20-6:40/km", "build1": "6:05-6:25/km", "build2": "5:50-6:10/km",
            "peak": "5:45-6:05/km", "taper": "5:50-6:10/km", "race": "6:00-6:20/km"}[phase]


def quality_session(phase: str, w: int) -> tuple[str, str]:
    if phase == "base":
        return "Rolig tempo-tur", "Sidste 10-15 min i " + "5:45-6:05/km, resten roligt"
    if phase == "build1":
        return "Tempo-intervaller", "4-6 x 4 min i 5:35-5:55/km, 2 min let jog imellem"
    if phase in ("build2", "peak"):
        return "Race-pace intervaller", f"5-8 x 1 km i {RACE_PACE}, 2 min let jog imellem"
    if phase == "taper":
        return "Kort skarphed", f"4 x 3 min i {RACE_PACE}, god pause imellem"
    return "Opvarmning til løbet", "20-30 min let jog med et par stryg"


def bike_minutes(phase: str, kind: str) -> int:
    base = {"base": 35, "build1": 45, "build2": 50, "peak": 55, "taper": 30, "race": 20}[phase]
    if kind == "bike_quality":
        return base
    if kind == "bike_recovery":
        return max(20, base - 15)
    return base


def bike_desc(kind: str, phase: str) -> str:
    if kind == "bike_quality":
        if phase in ("base",):
            return "Rolig kadence-tur, hold pulsen nede"
        return "Zwift intervaller: 5-8 x 3 min hårdt / 3 min let"
    if kind == "bike_recovery":
        return "Meget let restitutionstur, saml ben"
    return "Zwift grundtræning, jævnt roligt tempo (zone 2)"


def strength_desc(kind: str) -> str:
    return {
        "strength_push": "Push: bænkpres/skråbænk, skulderpres, dips/triceps, 3-4 sæt x 6-12 reps",
        "strength_pull": "Pull: markløft/roning, pull-ups/latpulldown, biceps, 3-4 sæt x 6-12 reps",
        "strength_legs": "Legs: squat, rumænske markløft, udfald, cordbeen, core, 3-4 sæt x 6-12 reps",
    }[kind]


def build_weekly_rows():
    rows = []
    for w in range(1, 45):
        week_start = PLAN_START_MONDAY + timedelta(weeks=w - 1)
        phase = phase_for_week(w)
        long_km = long_run_km(w, phase)
        easy_km = easy_run_km(long_km, phase)
        qual_km = quality_run_km(long_km, phase)
        qual_title, qual_desc = quality_session(phase, w)
        rows.append({
            "week_start": week_start.isoformat(),
            "week_number": w,
            "phase": phase,
            "cutback": is_cutback(w, phase),
            "run_sessions": 3,
            "run_km_target": round(long_km + easy_km + qual_km, 1),
            "long_run_km": long_km,
            "key_session": qual_title,
            "easy_pace": easy_pace(phase),
            "bike_sessions": 4,
            "bike_minutes_target": bike_minutes(phase, "bike_endurance") * 3 + bike_minutes(phase, "bike_quality"),
            "strength_push": 2,
            "strength_pull": 2,
            "strength_legs": 1,
            "steps_goal": STEPS_GOAL,
            "notes": qual_desc,
        })
    return rows


def classify(type_str: str) -> str:
    t = (type_str or "").lower()
    if "run" in t:
        return "run"
    if "cycl" in t or "bik" in t:
        return "bike"
    if "strength" in t or "weight" in t:
        return "strength"
    return "other"


def supabase_get(table: str, select: str, filters: str = "") -> list[dict]:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return []
    url = f"{SUPABASE_URL}/rest/v1/{table}?select={select}"
    if filters:
        url += f"&{filters}"
    req = urllib.request.Request(
        url, headers={"apikey": SUPABASE_SERVICE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        print(f"Could not fetch {table} for adherence check: {exc}", file=sys.stderr)
        return []


def recent_adherence(lookback_days: int = 7) -> tuple[float, int]:
    """How much of the last week's plan actually happened, judged by whether a
    same-day, same-discipline Garmin activity exists. Returns (fraction, n_planned)."""
    since = (TODAY - timedelta(days=lookback_days)).isoformat()
    planned = supabase_get("plan_daily", "date,discipline", f"date=gte.{since}&date=lt.{TODAY.isoformat()}")
    if not planned:
        return 1.0, 0
    activities = supabase_get("garmin_activities", "start,type", f"start=gte.{since}")
    done_by_group = {}
    for a in activities:
        g = classify(a.get("type"))
        done_by_group.setdefault(g, set()).add((a.get("start") or "")[:10])
    hits = sum(1 for p in planned if p["date"] in done_by_group.get(p["discipline"].split("_")[0], set()))
    return hits / len(planned), len(planned)


def adjust_factor(adherence: float) -> float:
    """Pull upcoming run/bike volume back when recent sessions were mostly missed,
    instead of blindly progressing the fixed macro formula regardless of what
    actually happened."""
    if adherence >= 0.85:
        return 1.0
    if adherence >= 0.6:
        return 0.9
    if adherence >= 0.4:
        return 0.75
    return 0.6


def build_daily_rows(days: int = 7):
    weekly_by_monday = {r["week_start"]: r for r in build_weekly_rows()}
    adherence, n_planned = recent_adherence()
    factor = adjust_factor(adherence)
    adjust_note = ""
    if n_planned and factor < 1.0:
        adjust_note = f" (justeret ned {round((1 - factor) * 100)}% -- {round(adherence * 100)}% af sidste uges pas blev gennemført)"

    rows = []
    for i in range(days):
        d = TODAY + timedelta(days=i)
        monday = d - timedelta(days=d.weekday())
        week = weekly_by_monday.get(monday.isoformat())
        if week is None:
            continue
        phase = week["phase"]
        for kind in WEEKDAY_TEMPLATE[d.weekday()]:
            if kind == "run_long":
                distance = round(week["long_run_km"] * factor, 1)
                title, desc = "Langtur", f"{distance} km i {week['easy_pace']}{adjust_note}"
                duration = None
            elif kind == "run_easy":
                distance = round(easy_run_km(week["long_run_km"], phase) * factor, 1)
                title, desc = "Rolig løbetur", f"~{distance} km i {week['easy_pace']}{adjust_note}"
                duration = None
            elif kind == "run_quality":
                distance = round(quality_run_km(week["long_run_km"], phase) * factor, 1)
                title, desc = week["key_session"], week["notes"] + adjust_note
                duration = None
            elif kind in ("bike_endurance", "bike_quality", "bike_recovery"):
                title = {"bike_endurance": "Zwift grundtur", "bike_quality": "Zwift intervaller",
                         "bike_recovery": "Zwift restitution"}[kind]
                desc = bike_desc(kind, phase) + adjust_note
                distance = None
                duration = round(bike_minutes(phase, kind) * factor)
            else:  # strength -- not volume-adjusted, consistency matters more here
                title = {"strength_push": "Styrke: Push", "strength_pull": "Styrke: Pull",
                         "strength_legs": "Styrke: Legs"}[kind]
                desc = strength_desc(kind)
                distance = None
                duration = 45

            rows.append({
                "date": d.isoformat(),
                "discipline": kind,
                "title": title,
                "prescription": desc,
                "target_distance_km": distance,
                "target_duration_min": duration,
                "completed": False,
            })
    return rows


def supabase_upsert(table: str, conflict_cols: str, rows: list[dict]):
    if not rows:
        return
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_KEY first.", file=sys.stderr)
        sys.exit(1)
    endpoint = f"{SUPABASE_URL}/rest/v1/{table}?on_conflict={conflict_cols}"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(rows).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(f"{table}: sent {len(rows)} rows -> HTTP {resp.status}")


if __name__ == "__main__":
    weekly = build_weekly_rows()
    daily = build_daily_rows()
    if "--dry-run" in sys.argv:
        print(json.dumps({"weekly_count": len(weekly), "daily_count": len(daily),
                           "weekly_sample": weekly[:3], "daily_sample": daily[:5]}, indent=2))
    else:
        supabase_upsert("plan_weekly", "week_start", weekly)
        supabase_upsert("plan_daily", "date,discipline", daily)

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


EASY_PACE_RANGE_SEC = {
    "base": (380, 400), "build1": (365, 385), "build2": (350, 370),
    "peak": (345, 365), "taper": (350, 370), "race": (360, 380),
}  # seconds/km -- matches the strings below exactly, kept numeric so it's adjustable


def format_pace_range(lo_sec: float, hi_sec: float) -> str:
    def fmt(s):
        m, sec = divmod(round(s), 60)
        return f"{m}:{sec:02d}"
    return f"{fmt(lo_sec)}-{fmt(hi_sec)}/km"


def easy_pace(phase: str, offset_sec: float = 0) -> str:
    lo, hi = EASY_PACE_RANGE_SEC[phase]
    return format_pace_range(lo + offset_sec, hi + offset_sec)


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
        "strength_push": "Push: bænkpres, cable crossover, triceps extension (row), triceps extension (bar). 3-4 sæt x 6-12 reps.",
        "strength_pull": "Pull: pull-up, rows, military rows, hammer curls. 3-4 sæt x 6-12 reps (pull-up: så mange reps du kan).",
        "strength_legs": "Legs: bulgarian split squats, calf raises. 3-4 sæt x 6-12 reps.",
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


def classify(activity: dict) -> str:
    t = (activity.get("type") or "").lower()
    n = (activity.get("name") or "").lower()
    if "run" in t:
        return "run"
    # Garmin labels indoor/Zwift rides "virtual_ride" (no "cycl"/"bik" in it),
    # so also match "ride" and fall back to the activity name.
    if "cycl" in t or "bik" in t or "ride" in t or "zwift" in n:
        return "bike"
    if "strength" in t or "weight" in t:
        return "strength"
    return "other"


def pace_sec_per_km(distance_km, duration_min):
    if not distance_km or not duration_min:
        return None
    return (duration_min * 60) / distance_km


def format_pace(sec_per_km) -> "str | None":
    if sec_per_km is None:
        return None
    minutes, seconds = divmod(round(sec_per_km), 60)
    return f"{minutes}:{seconds:02d}/km"


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


def session_execution_score(planned_row: dict, matched: list[dict]) -> float:
    """0..1.2: how much of the prescribed distance/duration actually happened
    for one session, not just whether it happened at all."""
    if not matched:
        return 0.0
    total_distance = sum(a.get("distance_km") or 0 for a in matched)
    total_duration = sum(a.get("duration_min") or 0 for a in matched)
    target_distance = planned_row.get("target_distance_km")
    target_duration = planned_row.get("target_duration_min")
    if target_distance:
        ratio = (total_distance / target_distance) if target_distance else 1.0
    elif target_duration:
        ratio = (total_duration / target_duration) if target_duration else 1.0
    else:
        ratio = 1.0
    return round(min(1.2, ratio), 2)  # cap credit for overdoing the volume


def recent_execution(lookback_days: int = 7) -> tuple[float, int, "str | None"]:
    """How well the last week's plan actually went: for each planned session,
    how much of the prescribed distance/duration was actually covered (not
    just yes/no), averaged across the week. Strength sessions stay yes/no --
    Garmin can't tell push from pull from legs, so presence (a same-day
    activity, or a logged exercise for that date+discipline) is all we have.
    Returns (avg_execution, n_planned, average run pace last week or None).
    """
    since = (TODAY - timedelta(days=lookback_days)).isoformat()
    planned = supabase_get(
        "plan_daily", "date,discipline,target_distance_km,target_duration_min",
        f"date=gte.{since}&date=lt.{TODAY.isoformat()}",
    )
    if not planned:
        return 1.0, 0, None

    activities = supabase_get(
        "garmin_activities", "start,type,name,distance_km,duration_min", f"start=gte.{since}"
    )
    strength_logged = supabase_get("strength_log", "date,discipline", f"date=gte.{since}")
    logged_dates = {(row["date"], row.get("discipline")) for row in strength_logged}

    by_group_date: dict[tuple, list] = {}
    for a in activities:
        key = (classify(a), (a.get("start") or "")[:10])
        by_group_date.setdefault(key, []).append(a)

    scores = []
    for p in planned:
        group = p["discipline"].split("_")[0]
        matched = by_group_date.get((group, p["date"]), [])
        if group == "strength":
            done = bool(matched) or (p["date"], p["discipline"]) in logged_dates
            scores.append(1.0 if done else 0.0)
        else:
            scores.append(session_execution_score(p, matched))

    run_paces = [
        pace_sec_per_km(a.get("distance_km"), a.get("duration_min"))
        for a in activities if classify(a) == "run"
    ]
    run_paces = [p for p in run_paces if p is not None]
    avg_run_pace = format_pace(sum(run_paces) / len(run_paces)) if run_paces else None

    avg_execution = sum(scores) / len(scores) if scores else 1.0
    return avg_execution, len(planned), avg_run_pace


def adjust_factor(execution: float) -> float:
    """Pull upcoming run/bike volume back when recent sessions were under-
    executed (missed, cut short, or otherwise below prescription) -- or nudge
    it up slightly when consistently over-delivered -- instead of blindly
    progressing the fixed macro formula regardless of what actually happened.
    """
    if execution >= 1.05:
        return 1.05
    if execution >= 0.85:
        return 1.0
    if execution >= 0.6:
        return 0.9
    if execution >= 0.4:
        return 0.75
    return 0.6


def current_phase(weekly_by_monday: dict) -> str:
    monday = (TODAY - timedelta(days=TODAY.weekday())).isoformat()
    week = weekly_by_monday.get(monday)
    return week["phase"] if week else "base"


def recent_easy_pace_offset(phase: str, lookback_days: int = 14) -> "float | None":
    """Seconds/km your actual easy/long-run pace has drifted from what this
    phase's easy-pace band assumes. Positive = you're running slower than
    expected (ease the target off); negative = faster (target can tighten a
    little). Only easy/long days count -- quality sessions are deliberately
    faster and aren't a fitness signal for the *easy* pace. Capped
    asymmetrically: generous if you're struggling, conservative if you're
    flying, since "easy" should stay easy rather than creep into tempo."""
    since = (TODAY - timedelta(days=lookback_days)).isoformat()
    planned = supabase_get(
        "plan_daily", "date,discipline",
        f"date=gte.{since}&date=lt.{TODAY.isoformat()}&discipline=in.(run_easy,run_long)",
    )
    if not planned:
        return None
    activities = supabase_get(
        "garmin_activities", "start,type,name,distance_km,duration_min", f"start=gte.{since}"
    )
    by_date = {}
    for a in activities:
        if classify(a) == "run":
            by_date.setdefault((a.get("start") or "")[:10], []).append(a)

    paces = []
    for p in planned:
        for a in by_date.get(p["date"], []):
            pace = pace_sec_per_km(a.get("distance_km"), a.get("duration_min"))
            if pace:
                paces.append(pace)
    if not paces:
        return None

    expected_mid = sum(EASY_PACE_RANGE_SEC[phase]) / 2
    offset = (sum(paces) / len(paces)) - expected_mid
    return max(-15, min(30, offset))


def build_daily_rows(days: int = 7):
    weekly_by_monday = {r["week_start"]: r for r in build_weekly_rows()}
    execution, n_planned, avg_run_pace = recent_execution()
    factor = adjust_factor(execution)

    phase_now = current_phase(weekly_by_monday)
    pace_offset = recent_easy_pace_offset(phase_now) or 0
    apply_pace_offset = abs(pace_offset) >= 5

    adjust_note = ""
    if n_planned:
        if factor < 1.0:
            adjust_note = f" (justeret ned {round((1 - factor) * 100)}% -- sidste uges pas blev i snit kun {round(execution * 100)}% gennemført)"
        elif factor > 1.0:
            adjust_note = " (justeret lidt op -- du overpræsterede sidste uges volumen)"
        if avg_run_pace:
            adjust_note += f" [snit løbepace sidste uge: {avg_run_pace}]"
    if apply_pace_offset:
        direction = "langsommere" if pace_offset > 0 else "hurtigere"
        adjust_note += f" [rolig-tempo justeret {direction} ud fra din faktiske pace sidste 14 dage]"

    rows = []
    for i in range(days):
        d = TODAY + timedelta(days=i)
        monday = d - timedelta(days=d.weekday())
        week = weekly_by_monday.get(monday.isoformat())
        if week is None:
            continue
        phase = week["phase"]
        day_easy_pace = easy_pace(phase, pace_offset if apply_pace_offset else 0)
        for kind in WEEKDAY_TEMPLATE[d.weekday()]:
            if kind == "run_long":
                distance = round(week["long_run_km"] * factor, 1)
                title, desc = "Langtur", f"{distance} km i {day_easy_pace}{adjust_note}"
                duration = None
            elif kind == "run_easy":
                distance = round(easy_run_km(week["long_run_km"], phase) * factor, 1)
                title, desc = "Rolig løbetur", f"~{distance} km i {day_easy_pace}{adjust_note}"
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

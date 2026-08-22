#!/usr/bin/env python3
"""Pull your own Garmin Connect data into plain-English files or your own endpoint.

Thin wrapper around cyberjunky/python-garminconnect:
https://github.com/cyberjunky/python-garminconnect

Usage:
    python sync_garmin.py --login                 # one-time interactive login
    python sync_garmin.py --days 3 --dry-run       # test: print, write/send nothing
    python sync_garmin.py --days 3 --sink files    # write garmin/ folder
    python sync_garmin.py --days 3 --sink supabase # POST to your own ingest endpoint
    python sync_garmin.py --export-ci-token        # bundle saved login for GitHub Actions

Security:
    Your password is only ever typed into a hidden terminal prompt during
    --login. It is never accepted via a flag or environment variable, never
    logged, and never written to disk. Only the resulting login token is
    saved (privately, with restrictive file permissions) so future runs
    don't need your password again.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import io
import json
import os
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import warnings
from datetime import date, timedelta
from pathlib import Path

try:
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectTooManyRequestsError,
    )
except ImportError:
    print(
        "The garminconnect library isn't installed yet.\n"
        "Run: pip install -r requirements.txt",
        file=sys.stderr,
    )
    sys.exit(1)

GARMIN_LOGIN_ERRORS = (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

DEFAULT_TOKENSTORE = Path(
    os.environ.get("GARMIN_AI_TOKENSTORE", str(Path.home() / ".garmin-ai" / "tokens"))
)
DEFAULT_OUT = Path("garmin")
CI_TOKEN_ENV = "GARMIN_TOKEN_B64"
CI_TOKEN_FILE = "garmin-ci-token.txt"


def fail(message: str, code: int = 1) -> "None":
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(code)


# --------------------------------------------------------------------------
# Login / token handling
# --------------------------------------------------------------------------


def require_real_terminal() -> None:
    if not sys.stdin.isatty():
        fail(
            "--login needs a real terminal so your password can be hidden as you "
            "type it. Run this directly in Terminal (Mac/Linux) or PowerShell/cmd "
            "(Windows) -- not piped, not redirected, not from an IDE task runner."
        )


def prompt_mfa_code() -> str:
    return input("Garmin just asked for a 2FA code -- enter it here: ").strip()


def read_hidden_password() -> str:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", getpass.GetPassWarning)
        password = getpass.getpass("Garmin password (hidden, never stored or shown): ")
    if any(issubclass(w.category, getpass.GetPassWarning) for w in caught):
        fail(
            "This terminal can't hide typed characters, so I'm refusing to read "
            "your password here. Run this from a real terminal instead."
        )
    return password


def lock_down(path: Path) -> None:
    """Best-effort: make sure only you can read the saved token."""
    if not path.exists():
        return
    try:
        if path.is_dir():
            for root, _dirs, files in os.walk(path):
                os.chmod(root, 0o700)
                for name in files:
                    os.chmod(os.path.join(root, name), 0o600)
        else:
            os.chmod(path, 0o600)
    except OSError:
        pass


def do_login(tokenstore: Path) -> None:
    require_real_terminal()

    email = input("Garmin email: ").strip()
    if not email:
        fail("No email entered.")

    password = read_hidden_password()
    if not password:
        fail("No password entered.")

    tokenstore.parent.mkdir(parents=True, exist_ok=True)

    try:
        client = Garmin(email, password, prompt_mfa=prompt_mfa_code)
        client.login(str(tokenstore))
    except GARMIN_LOGIN_ERRORS as exc:
        fail(f"Garmin login failed ({exc.__class__.__name__}). Check your email/password and try again.")
    except Exception as exc:  # noqa: BLE001 - surface anything unexpected, but don't leak the password
        fail(f"Login failed unexpectedly ({exc.__class__.__name__}).")
    finally:
        password = None  # noqa: F841 - drop the reference as soon as we're done with it

    lock_down(tokenstore)
    print(f"Logged in. Your login token is saved privately at {tokenstore}.")
    print("It's never printed here, and you shouldn't need to log in again for about a year.")


def load_client(tokenstore: Path) -> Garmin:
    if not tokenstore.exists():
        fail("No saved Garmin login found. Run this once first:\n  python sync_garmin.py --login")
    client = Garmin()
    try:
        client.login(str(tokenstore))
    except Exception as exc:  # noqa: BLE001
        fail(
            "Your saved login didn't work (it may have expired). Run:\n"
            f"  python sync_garmin.py --login\n({exc.__class__.__name__})"
        )
    return client


def load_client_from_ci_token(token_b64: str) -> Garmin:
    try:
        raw = base64.b64decode(token_b64.encode("ascii"))
    except Exception:  # noqa: BLE001
        fail(f"{CI_TOKEN_ENV} doesn't look like a valid token bundle.")

    tmpdir = Path(tempfile.mkdtemp(prefix="garmin-ci-"))
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        tar.extractall(tmpdir)  # nosec - contents are our own export, not user-supplied
    lock_down(tmpdir)

    client = Garmin()
    try:
        client.login(str(tmpdir))
    except Exception as exc:  # noqa: BLE001
        fail(
            f"{CI_TOKEN_ENV} didn't work ({exc.__class__.__name__}). "
            "Re-run --login locally and refresh the GitHub secret with --export-ci-token."
        )
    return client


def export_ci_token(tokenstore: Path) -> None:
    if not tokenstore.exists():
        fail("No saved login yet. Run --login first.")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(tokenstore, arcname=".")
    token_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    out_path = Path(CI_TOKEN_FILE)
    out_path.write_text(token_b64 + "\n")
    os.chmod(out_path, 0o600)

    print(f"Wrote {out_path}.")
    print(f"Paste its contents into the {CI_TOKEN_ENV} GitHub secret, then delete this file.")
    print("It's a login credential -- never commit it, never paste it anywhere else.")


# --------------------------------------------------------------------------
# Pulling data
# --------------------------------------------------------------------------


def daterange(end: date, days: int) -> list[date]:
    return [end - timedelta(days=i) for i in range(days - 1, -1, -1)]


def as_int(value) -> "int | None":
    """Garmin's API mixes int/float for whole-number fields (e.g. 153.0), but
    the database columns are integer -- Postgres rejects "153.0" as input for
    an integer column even though it's numerically whole. Normalize here."""
    return None if value is None else int(round(value))


def fetch_wellness(client: Garmin, day: date) -> dict:
    cdate = day.isoformat()
    out: dict = {"date": cdate}

    try:
        summary = client.get_user_summary(cdate) or {}
    except Exception:  # noqa: BLE001
        summary = {}
    out["resting_hr"] = as_int(summary.get("restingHeartRate"))
    out["steps"] = as_int(summary.get("totalSteps"))
    out["stress_avg"] = as_int(summary.get("averageStressLevel"))
    out["body_battery_low"] = as_int(summary.get("bodyBatteryLowestValue"))
    out["body_battery_high"] = as_int(summary.get("bodyBatteryHighestValue"))

    try:
        sleep = client.get_sleep_data(cdate) or {}
        dto = sleep.get("dailySleepDTO") or {}
        seconds = dto.get("sleepTimeSeconds")
        out["sleep_hours"] = round(seconds / 3600, 1) if seconds else None
        overall = (dto.get("sleepScores") or {}).get("overall") or {}
        out["sleep_score"] = as_int(overall.get("value"))
    except Exception:  # noqa: BLE001
        out["sleep_hours"] = None
        out["sleep_score"] = None

    try:
        hrv = client.get_hrv_data(cdate) or {}
        hrv_summary = hrv.get("hrvSummary") or {}
        out["hrv"] = hrv_summary.get("lastNightAvg") or hrv_summary.get("weeklyAvg")
    except Exception:  # noqa: BLE001
        out["hrv"] = None

    try:
        readiness = client.get_training_readiness(cdate)
        if isinstance(readiness, list) and readiness:
            out["training_readiness"] = as_int(readiness[0].get("score"))
        elif isinstance(readiness, dict):
            out["training_readiness"] = as_int(readiness.get("score"))
        else:
            out["training_readiness"] = None
    except Exception:  # noqa: BLE001
        out["training_readiness"] = None

    return out


def fetch_activities(client: Garmin, start: date, end: date) -> list[dict]:
    try:
        activities = client.get_activities_by_date(start.isoformat(), end.isoformat()) or []
    except Exception:  # noqa: BLE001
        activities = []

    cleaned = []
    for a in activities:
        duration = a.get("duration")
        distance = a.get("distance")
        cleaned.append(
            {
                "id": a.get("activityId"),
                "name": a.get("activityName"),
                "type": (a.get("activityType") or {}).get("typeKey"),
                "start": a.get("startTimeLocal"),
                "duration_min": round(duration / 60, 1) if duration else None,
                "distance_km": round(distance / 1000, 2) if distance else None,
                "avg_hr": as_int(a.get("averageHR")),
                "calories": as_int(a.get("calories")),
            }
        )
    return cleaned


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def fmt(value, suffix: str = "", none_text: str = "not available") -> str:
    return none_text if value is None else f"{value}{suffix}"


def slugify(text: str | None) -> str:
    text = (text or "activity").lower()
    slug = "".join(ch if ch.isalnum() else "-" for ch in text).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "activity"


def daily_note_text(w: dict) -> str:
    body_battery = "not available"
    if w.get("body_battery_low") is not None and w.get("body_battery_high") is not None:
        body_battery = f"{w['body_battery_low']} -> {w['body_battery_high']}"

    sleep = "not available"
    if w.get("sleep_hours") is not None:
        score = f" (score {w['sleep_score']})" if w.get("sleep_score") is not None else ""
        sleep = f"{w['sleep_hours']} h{score}"

    lines = [
        f"# Garmin wellness {w['date']}",
        f"- Resting HR: {fmt(w.get('resting_hr'), ' bpm')}",
        f"- HRV (overnight): {fmt(w.get('hrv'), ' ms')}",
        f"- Sleep: {sleep}",
        f"- Body battery: {body_battery}",
        f"- Stress (avg): {fmt(w.get('stress_avg'))}",
        f"- Steps: {fmt(w.get('steps'))}",
        f"- Training readiness: {fmt(w.get('training_readiness'))}",
        "",
    ]
    return "\n".join(lines)


def activity_note_text(a: dict) -> str:
    lines = [
        f"# {a.get('name') or 'Activity'} ({a.get('type') or 'unknown'})",
        f"- Date: {a.get('start') or 'not available'}",
        f"- Duration: {fmt(a.get('duration_min'), ' min')}",
        f"- Distance: {fmt(a.get('distance_km'), ' km')}",
        f"- Avg HR: {fmt(a.get('avg_hr'), ' bpm')}",
        f"- Calories: {fmt(a.get('calories'))}",
        "",
    ]
    return "\n".join(lines)


def load_store(out_dir: Path) -> dict:
    path = out_dir / "data.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"activities": {}, "wellness": {}}


def save_store(out_dir: Path, store: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "data.json").write_text(json.dumps(store, indent=2, sort_keys=True))


def sink_files(out_dir: Path, activities: list[dict], wellness: list[dict]) -> None:
    store = load_store(out_dir)

    daily_dir = out_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    for w in wellness:
        (daily_dir / f"{w['date']}.md").write_text(daily_note_text(w))
        store["wellness"][w["date"]] = w

    activities_dir = out_dir / "activities"
    activities_dir.mkdir(parents=True, exist_ok=True)
    for a in activities:
        if a.get("id") is None:
            continue
        day = (a.get("start") or "unknown")[:10]
        slug = slugify(a.get("name"))
        (activities_dir / f"{day}-{slug}.md").write_text(activity_note_text(a))
        store["activities"][str(a["id"])] = a

    save_store(out_dir, store)
    print(f"Wrote {len(wellness)} daily note(s) and {len(activities)} activity note(s) to {out_dir}/")


def _supabase_upsert(base_url: str, key: str, table: str, conflict_col: str, rows: list[dict]) -> None:
    if not rows:
        return
    endpoint = f"{base_url.rstrip('/')}/rest/v1/{table}?on_conflict={conflict_col}"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(rows).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            pass
    except urllib.error.HTTPError as exc:
        fail(f"Supabase rejected the {table} upsert (HTTP {exc.code}): {exc.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as exc:
        fail(f"Could not reach Supabase: {exc}")


def sink_supabase(activities: list[dict], wellness: list[dict]) -> None:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        fail(
            "Set SUPABASE_URL and SUPABASE_SERVICE_KEY to use --sink supabase.\n"
            "(SUPABASE_SERVICE_KEY is the service_role key -- keep it secret, never put it in the dashboard.)"
        )

    activities_with_id = [a for a in activities if a.get("id") is not None]
    _supabase_upsert(url, key, "garmin_wellness", "date", wellness)
    _supabase_upsert(url, key, "garmin_activities", "id", activities_with_id)
    print(f"Sent {len(activities_with_id)} activities and {len(wellness)} wellness day(s) to Supabase.")


def dry_run_report(activities: list[dict], wellness: list[dict]) -> None:
    print(f"Would pull {len(wellness)} day(s) of wellness and {len(activities)} activity(ies). Nothing written or sent.\n")
    for w in wellness:
        print(
            f"  {w['date']}: RHR={fmt(w.get('resting_hr'))} HRV={fmt(w.get('hrv'))} "
            f"sleep={fmt(w.get('sleep_hours'), 'h' if w.get('sleep_hours') is not None else '')} "
            f"steps={fmt(w.get('steps'))} readiness={fmt(w.get('training_readiness'))}"
        )
    for a in activities:
        print(f"  activity: {a.get('start')} - {a.get('name')} ({a.get('type')})")


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pull your own Garmin data into files or your own database.")
    parser.add_argument("--login", action="store_true", help="One-time interactive login (the only place you type your password).")
    parser.add_argument("--export-ci-token", action="store_true", help=f"Write a base64 token bundle to {CI_TOKEN_FILE} for GitHub Actions.")
    parser.add_argument("--days", type=int, default=7, help="How many days back to pull (default 7).")
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen; write and send nothing.")
    parser.add_argument("--sink", choices=["files", "supabase"], default="files", help="Where to put the data (default files).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output folder for --sink files (default ./garmin).")
    parser.add_argument("--tokenstore", type=Path, default=DEFAULT_TOKENSTORE, help=argparse.SUPPRESS)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()

    if args.login:
        do_login(args.tokenstore)
        return

    if args.export_ci_token:
        export_ci_token(args.tokenstore)
        return

    ci_token = os.environ.get(CI_TOKEN_ENV)
    client = load_client_from_ci_token(ci_token) if ci_token else load_client(args.tokenstore)

    end = date.today()
    start = end - timedelta(days=args.days - 1)

    wellness = [fetch_wellness(client, d) for d in daterange(end, args.days)]
    activities = fetch_activities(client, start, end)

    if args.dry_run:
        dry_run_report(activities, wellness)
        return

    if args.sink == "files":
        sink_files(args.out, activities, wellness)
    else:
        sink_supabase(activities, wellness)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        fail("Cancelled.", code=130)

# Connect your Garmin to AI (free setup)

> **Paste this into Claude Code, not a regular Claude chat.** This guide installs
> software and runs commands on your computer, which a normal chat window cannot
> do. Claude Code is the free coding agent that runs in your terminal and can
> actually do these steps for you.
>
> New to Claude Code? Here is the setup walkthrough:
> [Claude Code setup video](https://www.skool.com/athlete-ai-community/classroom/4d559c78?md=9ff91bc6878648d382f7b1cac766d504)

Pull your own Garmin data (workouts plus sleep, HRV, resting HR, body battery,
stress, training readiness) into a folder your AI coach reads, or into your own
database. This is the recovery data Strava cannot give you.

This is built on the open-source **python-garminconnect** library by cyberjunky.
The script here is a thin wrapper around it. Full library and docs:
[github.com/cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect)

> **Security-hardened.** Your password is typed once into a hidden prompt and is
> never stored, never put in an environment variable, never left in your command
> history, and never printed. Your year-long login token is saved privately and
> never shown on screen. The script even refuses to run where your password
> can't be hidden.

## The easy way: paste this into Claude Code and let it build everything

You do not download anything, make any folders, or write any code. Claude Code does
all of it. Open Claude Code, paste the prompt below, and send it. It walks you
through the whole thing one step at a time, and the only thing you ever type is your
Garmin login (once), because it's your account.

```text
I want to connect my Garmin watch so you can read my training and recovery data,
and I'm starting from scratch. I don't want to download anything, create any
folders, or write any code myself. You do all of it. Walk me through it one step
at a time, in plain English, and stop to wait for me whenever you need something
from me.

The goal: pull my own Garmin data (my workouts plus my recovery numbers: sleep,
HRV, resting heart rate, body battery, stress, and training readiness) into a
folder on my computer that you can read every time I ask. This is the recovery
data Strava can't give you. Read-only, you should never write anything back to my
Garmin account.

Here is everything I need you to do, end to end. Do it all yourself:

1. Make a new folder for this on my computer (something like "garmin-ai") and work
   inside it. I should not have to create it myself.
2. Write the sync script for me. Use the open-source python-garminconnect library
   by cyberjunky (https://github.com/cyberjunky/python-garminconnect). The script
   should pull my recent activities plus my daily wellness (sleep, HRV, resting
   HR, body battery, stress, steps, training readiness) and save them as a clean
   "garmin/" folder of plain-English markdown notes plus a data.json file. One
   wellness note per day, one note per workout.
3. Write the requirements.txt and install whatever is needed. Check whether I
   already have Python first, and help me install it if I don't.
4. Do the one-time Garmin login. Stop and ask me for my Garmin email and password
   when you reach this step, that is the only thing I will type. Handle the 2FA
   code if Garmin asks for one. Keep my password and login token out of this chat,
   never print them back to me, and save the login token so I don't have to log in
   again next time.
5. Test it by pulling my last 3 days and showing me the results so I know it works.
6. Once that works, ask me whether I want it to run automatically every morning,
   and if I say yes, set that up for me too.

Go one step at a time. Don't dump everything at once, and don't make me touch any
files, folders, or code myself. You handle all of that. When you need me to
approve a command, just ask and I'll say yes.
```

That is the whole setup. Everything below is reference: what the data looks like,
how to schedule it, and the manual steps if you would rather build it by hand or
understand what Claude made for you.

---

## Manual setup (by hand, if you skip the prompt above)

The [`files/`](files/) folder next to this guide has everything: `sync_garmin.py`
(the pull script), `requirements.txt` (what to install), and `garmin-sync.yml` (the
optional cloud automation for Path A). Copy `files/sync_garmin.py` and
`files/requirements.txt` into a folder on your computer, for example `garmin-ai/`,
and open a terminal there.

Then run the one-time setup:

1. Install Python 3.11+ from python.org, then install the library:

   ```bash
   pip install -r requirements.txt
   ```

   On Windows, if `python`/`pip` is not found, use the `py` launcher:
   `py -m pip install -r requirements.txt`.

2. Log in once. This is the only time you enter your password or a 2FA code.
   Run it from a real terminal (Terminal on Mac, PowerShell or cmd on Windows):

   ```bash
   python sync_garmin.py --login
   ```

   ```powershell
   py sync_garmin.py --login
   ```

   It asks for your Garmin email, then your password in a **hidden prompt**
   (nothing shows as you type), then a 2FA code if Garmin asks. Your password is
   never stored and never printed. It saves a login token on your computer that
   lasts about a year -- also never printed to the screen.

   **Only if you're using Path A (GitHub Actions):** after login, run
   `python sync_garmin.py --export-ci-token`. That writes the token bundle to a
   file called `garmin-ci-token.txt`. Paste its contents into your
   `GARMIN_TOKEN_B64` GitHub secret, then delete the file. (Path B / local users
   never need this.)

3. Test it:

   ```bash
   python sync_garmin.py --days 3 --dry-run
   ```

   You should see your last 3 days of activities and wellness print out.

---

## What you get

By default the script writes a clean folder your AI can read:

```text
garmin/
  daily/2026-06-28.md          # one wellness note per day, plain English
  activities/2026-06-28-...md   # one note per workout
  data.json                    # the full store, updated each run
```

A daily note looks like this:

```text
# Garmin wellness 2026-06-28
- Resting HR: 48 bpm
- HRV (overnight): 72 ms
- Sleep: 7.7 h (score 84)
- Body battery: 28 -> 96
- Stress (avg): 31
- Steps: 11240
- Training readiness: 81
```

Point your AI coach at the `garmin/` folder and it has your recovery context every
morning. (Sleep and HRV only fill in on nights you actually wear the watch to bed.)

---

## Path A: GitHub Actions + Supabase + a phone dashboard (cloud, automatic)

This is the "check it from my phone every morning" setup. Three pieces:
Supabase (free database, doubles as the ingest API — no backend code to write),
GitHub Actions (runs the sync every morning, no computer needs to stay on), and
a small dashboard page hosted on GitHub Pages (fetches live from Supabase each
time you open it, so it's always current with zero deploy step after setup).

**Heads up on privacy:** the dashboard page is *unlisted*, not password-secure
in a strong sense — it's a static page gated by a passphrase check that lives in
the page's own source, which is enough to stop a casual visitor but not a
determined one. Good enough for "nobody stumbles onto my recovery data," not a
substitute for real auth. Say so if you want something stronger.

1. **Create a free Supabase project** at [supabase.com](https://supabase.com).
   Once it's created, open **SQL Editor -> New query**, paste in the contents of
   [`files/supabase_schema.sql`](files/supabase_schema.sql), and run it. That
   creates the two tables the sync script writes to.

2. Open **Project Settings -> API** and note down two values:
   - **Project URL**
   - **anon public** key (safe to publish — it can only *read*, see the SQL
     policies) — this goes in the dashboard page
   - **service_role** key (**secret** — can write; only goes in the GitHub
     Actions secret below, never in the dashboard page)

3. In your GitHub repo: **Settings > Secrets and variables > Actions**, add:

   | Secret | Value |
   |--------|-------|
   | `GARMIN_TOKEN_B64` | the contents of `garmin-ci-token.txt` from `--export-ci-token` |
   | `SUPABASE_URL` | your Supabase Project URL |
   | `SUPABASE_SERVICE_KEY` | your Supabase **service_role** key |

   Copy `files/garmin-sync.yml` into a `.github/workflows/` folder in the repo if
   it isn't already there. Open the Actions tab and click **Run workflow** once
   to confirm a green run. After that it runs every morning on its own.

   If you'd rather skip the database and dashboard entirely, change the
   workflow's last step to `--sink files` instead — see Path B.

4. **Set up the dashboard.** Open `docs/index.html` and fill in the three
   placeholders near the top of the `<script>` block: `SUPABASE_URL`,
   `SUPABASE_ANON_KEY` (the **anon public** key, not service_role), and
   `PASSPHRASE_SHA256` — a SHA-256 hash of whatever passphrase you want to gate
   the page with (ask Claude to generate the hash for you, or use the snippet
   in the file's comments). Commit and push.

5. Turn on GitHub Pages: **Settings > Pages**, set source to this branch and
   the `/docs` folder. Give it a minute, then open the URL it gives you on your
   phone and enter your passphrase.

You only touch any of this again if your password changes or the yearly token
expires; then re-run `--login` and update the `GARMIN_TOKEN_B64` secret.

---

## Path B: Local cron (your computer, no GitHub)

After Step 1, schedule the script on your own machine.

**Mac / Linux:**

```bash
crontab -e
# run every morning at 6am:
0 6 * * * cd /path/to/garmin-ai && python sync_garmin.py --days 3 --sink files --out ./garmin
```

**Windows (Task Scheduler):** create a Basic Task that runs daily and calls:

```text
py C:\path\to\garmin-ai\sync_garmin.py --days 3 --sink files --out C:\path\to\garmin-ai\garmin
```

Your machine has to be on and awake at the scheduled time.

---

## Sending to Supabase instead of files

This is what Path A uses under the hood. Once you've created a Supabase project
and run `files/supabase_schema.sql` (see Path A, steps 1-2), you can run it
locally too:

```bash
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_SERVICE_KEY="your-service_role-key"
python sync_garmin.py --days 3 --sink supabase
```

It upserts rows straight into the `garmin_wellness` and `garmin_activities`
tables via Supabase's REST API, keyed by date / activity id, so re-running it
just fills in gaps rather than duplicating rows.

---

## Notes and limits

- This uses an unofficial login flow (Garmin has no public API). It works through
  the [python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
  library. If Garmin changes their login and it stops working, update the library:
  `pip install -U garminconnect`, then re-run `--login`.
- Read-only. The script never writes anything back to your Garmin account.
- Keep your token private. It is a login credential (about a year of access).
  Never post it, never commit it to git, never paste it into a chat.
- Your notes are plain text files. If this folder lives in Dropbox / OneDrive /
  iCloud, they sync to that cloud. Fine if that's what you want -- just know it.

## Local vs cloud (your choice, made on purpose)

- **Path B (local)** keeps everything on your machine. Nothing leaves your
  computer except the read-only calls to Garmin. Most private option.
- **Path A (cloud)** deliberately sends your data to your own ingest endpoint so
  it can feed a dashboard. That's a conscious trade: convenience and a live
  dashboard in exchange for your data leaving your machine. Pick the one that
  fits you -- both are supported, neither is hidden.

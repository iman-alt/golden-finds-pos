# Backups

Everything this shop knows — stock, prices, sales, who owes what — is in
one file: `instance/golden_finds.db`. If the computer is stolen or its
disk dies and that file is not somewhere else, the business loses all of
it at once.

This takes about ten minutes to set up. Do it before the shop starts
using the system, not after.

## The short version

1. Pick a folder that syncs to the cloud (OneDrive or Google Drive).
2. Set `BACKUP_DIR` to it.
3. Schedule `flask backup` to run daily.
4. **Restore one backup onto another computer and check it opens.**

Step 4 is the one people skip, and it is the one that decides whether any
of the others mattered.

## Taking a backup by hand

```bash
flask --app run backup
```

It writes a timestamped file into `BACKUP_DIR`, verifies it opens and has
the expected tables, and deletes all but the most recent 30.

It uses SQLite's online backup API, not a file copy. A plain copy of a
database in WAL mode can catch it mid-write and produce a file that looks
fine and will not open — and nobody finds out until the day they need it.
Backing up while the till is serving a customer is safe.

## Where to put the backups

Set `BACKUP_DIR` to a folder the cloud sync client watches:

```
BACKUP_DIR=C:\Users\<name>\OneDrive\golden-finds-backups
```

The internet is never needed to *use* the system. It is only needed to
*protect* it, and only whenever it happens to be available — the sync
client catches up on its own when the line comes back.

## Running it daily on Windows

Open Task Scheduler and create a task:

- **Trigger:** Daily, at a time the shop is closed (say 21:00).
- **Action:** Start a program.
- **Program:** the `python.exe` inside the project's `.venv\Scripts\`
- **Arguments:** `-m flask --app run backup`
- **Start in:** the project folder

Tick "Run whether user is logged on or not" so it still runs if the till
is signed out.

Or from PowerShell, adjusting the path:

```powershell
$dir = "C:\Users\Iman\Development\Github\point of sale\golden-finds-pos"
schtasks /create /tn "Golden Finds backup" /tr "'$dir\.venv\Scripts\python.exe' -m flask --app run backup" /sc daily /st 21:00
```

## Checking it is still happening

```bash
flask --app run list-backups
```

The dashboard also says so on its own: if the newest backup is more than
two days old, or there has never been one, the owner sees a warning at the
top of the screen. A backup job that quietly stopped running months ago is
the usual way a shop like this loses its data, so it is not left to
anyone to remember to check.

## Restoring

Stop the app first — a restore underneath a running app is refused, because
it would corrupt what the app is holding.

```bash
flask --app run list-backups
flask --app run restore "C:\...\golden-finds-backups\golden-finds_2026-09-12_2100.db"
```

The database being replaced is renamed to `*.before-restore-*.db` rather
than deleted, so restoring the wrong file is itself recoverable.

## Test the restore

An untested backup is not a backup. It is a file you hope is a backup.

Once, when you set this up, and again every few months:

1. Copy a backup file onto a different computer.
2. Install the project there.
3. Restore the file and start the app.
4. Sign in. Check the products and the recent sales are there.

If the shop's computer dies on a Friday, this is exactly the procedure
you will be running — and Friday is not when you want to discover a
problem with it.

## What is not backed up

- `instance/secret_key` — only signs session cookies. If it is lost,
  everyone is signed out once and signs back in. Nothing else breaks.
- Product images in `static/product_images/`. Back these up separately if
  you start using them.

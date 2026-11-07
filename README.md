# SubKeep

A private subscription and expense tracker that runs **on your own computer**.
No account, no cloud, no tracking: your data sits in one file on your machine.

- **Subscriptions**: Netflix, Spotify, your phone plan, software licences. Weekly, monthly, every 3 months or yearly.
- **Expenses**: one-off spending like groceries, fuel or a dinner out, browsable month by month.
- **Dashboard**: what you pay per month and per year, upcoming renewals, spending by category, and a 6-month trend.
- **Calendar**: a month view showing every renewal and expense on its day. Click a day for details or to add an expense on that date.
- **Themes**: Light, Dark, Match computer, **Aurora** (animated northern lights), **Sakura** (cherry-blossom pastels with drifting petals) and **Forest** (deep green and gold). Animations switch off if your OS is set to reduce motion.
- **Categories** you can change and **40 currencies**.
- **Backup and export**: a JSON backup you can restore, plus CSV for Excel or Google Sheets.

Works on **Windows, macOS and Linux**. All it needs is Python 3.9 or newer, with no extra packages.

## Getting started

1. Install Python 3.9 or newer from <https://www.python.org/downloads/> if you don't have it.
   On Windows, tick **"Add Python to PATH"** during setup.
2. Download this project (green **Code** button → **Download ZIP**) and unzip it.
3. Start it:
   - **Windows:** double-click `start-windows.bat`
   - **macOS / Linux:** run `./start-mac-linux.sh` in a terminal
   - **Any OS:** `python -m subkeep`

Your browser opens SubKeep at `http://127.0.0.1:8765`. Keep the small terminal window open while
you use it, and close that window (or press Ctrl+C) to stop.

### Options

```
python -m subkeep --port 9000          # use a different port
python -m subkeep --no-browser         # don't open a browser automatically
python -m subkeep --data-dir D:\money  # keep the database somewhere else
```

You can also set the `SUBKEEP_DATA_DIR` environment variable instead of `--data-dir`.

## Where is my data?

| OS      | Location |
|---------|----------|
| Windows | `%APPDATA%\SubKeep\subkeep.db` |
| macOS   | `~/Library/Application Support/SubKeep/subkeep.db` |
| Linux   | `~/.local/share/subkeep/subkeep.db` |

It's a standard SQLite file. To back up, use **Settings → Download backup**, or copy the file while SubKeep is closed.

## Privacy and security

- SubKeep only listens on `127.0.0.1`, so other devices on your network can't reach it.
- It never contacts the internet. No analytics, no fonts or scripts from a CDN.
- It rejects requests from other websites: it checks the Host header and only accepts JSON, and it sends a strict Content-Security-Policy.

## For developers

```
python -m unittest discover -s tests -t .
```

Project layout:

```
subkeep/
  __main__.py   command-line entry point (starts the server and opens the browser)
  server.py     tiny HTTP server and JSON API (standard library only)
  db.py         SQLite schema, validation, renewal-date maths, summaries, import/export
  static/       the web interface: plain HTML, CSS and JavaScript with no build step
tests/          unit and API tests
```

### API

| Method | Path | |
|---|---|---|
| GET | `/api/summary` | dashboard numbers |
| GET/POST | `/api/subscriptions` | list / create |
| PUT/DELETE | `/api/subscriptions/{id}` | update / delete |
| GET/POST | `/api/expenses[?month=YYYY-MM]` | list / create |
| PUT/DELETE | `/api/expenses/{id}` | update / delete |
| GET/POST | `/api/categories`, DELETE `/api/categories/{id}` | categories |
| GET | `/api/calendar?month=YYYY-MM` | renewals and expenses grouped by day |
| GET/PUT | `/api/settings` | currency, theme |
| GET | `/api/export/json`, `/api/export/subscriptions.csv`, `/api/export/expenses.csv` | exports |
| POST | `/api/import/json` | restore a backup (replaces all data) |

## Roadmap ideas

- Renewal reminders (desktop notifications)
- iCal export for calendar apps
- Recurring expenses and budgets per category
- Multiple currencies with conversion
- Import from bank CSV files
- Packaged installers (no Python needed)

## Acknowledgements

Inspired by [SubTrackr](https://github.com/bscott/subtrackr) (bscott/subtrackr). SubKeep is an
independent project and contains none of SubTrackr's code.

## License

[MIT](LICENSE)

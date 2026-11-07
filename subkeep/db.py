"""SQLite storage and business logic for SubKeep.

Everything here is plain Python + sqlite3 so it runs anywhere Python 3.9+ does.
"""

from __future__ import annotations

import calendar
import os
import sqlite3
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

CYCLES = ("weekly", "monthly", "quarterly", "yearly")
STATUSES = ("active", "trial", "paused", "cancelled")
THEMES = ("system", "light", "dark", "aurora", "sakura", "forest")

# How many times per year each billing cycle charges (used for monthly/yearly totals).
CYCLE_PER_YEAR = {"weekly": 52, "monthly": 12, "quarterly": 4, "yearly": 1}

DEFAULT_CATEGORIES = [
    "Entertainment",
    "Software",
    "Utilities",
    "Food & Groceries",
    "Transport",
    "Health",
    "Shopping",
    "Education",
    "Other",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    cost           REAL NOT NULL CHECK (cost >= 0),
    cycle          TEXT NOT NULL DEFAULT 'monthly',
    next_renewal   TEXT,
    status         TEXT NOT NULL DEFAULT 'active',
    category_id    INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    payment_method TEXT NOT NULL DEFAULT '',
    url            TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    description    TEXT NOT NULL,
    amount         REAL NOT NULL CHECK (amount >= 0),
    spent_on       TEXT NOT NULL,
    category_id    INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    payment_method TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_expenses_spent_on ON expenses(spent_on);
"""

SCHEMA_VERSION = 1


class ValidationError(ValueError):
    """Raised when user input is invalid. The message is safe to show the user."""


def default_data_dir() -> Path:
    """Per-user data folder that follows each OS's conventions."""
    override = os.environ.get("SUBKEEP_DATA_DIR")
    if override:
        return Path(override).expanduser()
    home = Path.home()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return base / "SubKeep"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "SubKeep"
    base = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return base / "subkeep"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_date(value, field: str, required: bool = False) -> str | None:
    if value in (None, ""):
        if required:
            raise ValidationError(f"{field} is required")
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ValidationError(f"{field} must be a date like 2026-01-31") from None


def _parse_money(value, field: str) -> float:
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number") from None
    if amount < 0 or amount != amount or amount > 1e9:  # also rejects NaN
        raise ValidationError(f"{field} must be between 0 and 1,000,000,000")
    return amount


def _text(value, field: str, max_len: int = 500, required: bool = False) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise ValidationError(f"{field} is required")
    if len(text) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters")
    return text


def add_months(d: date, months: int) -> date:
    """Add months, clamping to the last day of the month (Jan 31 + 1 month = Feb 28/29)."""
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def advance(d: date, cycle: str, anchor_day: int | None = None) -> date:
    """Move a renewal date forward by one billing cycle.

    anchor_day keeps month-based cycles on their original day of month, so a
    subscription that renews on the 31st goes Jan 31 -> Feb 28 -> Mar 31.
    """
    if cycle == "weekly":
        return d + timedelta(days=7)
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}[cycle]
    nxt = add_months(d, months)
    if anchor_day:
        last = calendar.monthrange(nxt.year, nxt.month)[1]
        nxt = nxt.replace(day=min(anchor_day, last))
    return nxt


def roll_forward(renewal: date, cycle: str, today: date) -> date:
    """Return the first renewal date that is today or later."""
    anchor = renewal.day
    # Guard against absurd loops (e.g. a weekly sub dated in year 1).
    for _ in range(5000):
        if renewal >= today:
            return renewal
        renewal = advance(renewal, cycle, anchor)
    return renewal


def monthly_cost(cost: float, cycle: str) -> float:
    return cost * CYCLE_PER_YEAR[cycle] / 12


class Store:
    """Thread-safe wrapper around one SQLite database file."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ setup
    def _migrate(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO categories(name) VALUES (?)",
                    [(c,) for c in DEFAULT_CATEGORIES],
                )
                self._conn.executemany(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    [("currency", "USD"), ("theme", "system")],
                )
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            # Future schema changes go here: `if version < 2: ...`

    def _rows(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # --------------------------------------------------------------- settings
    def get_settings(self) -> dict:
        return {r["key"]: r["value"] for r in self._rows("SELECT key, value FROM settings")}

    def update_settings(self, data: dict) -> dict:
        allowed = {
            "currency": lambda v: _text(v, "Currency", 3, required=True).upper(),
            "theme": lambda v: v if v in THEMES else "system",
        }
        with self._lock, self._conn:
            for key, value in data.items():
                if key in allowed:
                    self._conn.execute(
                        "INSERT INTO settings(key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (key, allowed[key](value)),
                    )
        return self.get_settings()

    # ------------------------------------------------------------- categories
    def list_categories(self) -> list[dict]:
        return self._rows("SELECT id, name FROM categories ORDER BY name COLLATE NOCASE")

    def create_category(self, data: dict) -> dict:
        name = _text(data.get("name"), "Category name", 60, required=True)
        with self._lock, self._conn:
            try:
                cur = self._conn.execute("INSERT INTO categories(name) VALUES (?)", (name,))
            except sqlite3.IntegrityError:
                raise ValidationError(f'A category named "{name}" already exists') from None
        return {"id": cur.lastrowid, "name": name}

    def delete_category(self, cat_id: int) -> bool:
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,)).rowcount > 0

    def _category_id(self, value) -> int | None:
        if value in (None, "", 0, "0"):
            return None
        try:
            cat_id = int(value)
        except (TypeError, ValueError):
            raise ValidationError("Category is invalid") from None
        if not self._rows("SELECT 1 FROM categories WHERE id = ?", (cat_id,)):
            raise ValidationError("Category does not exist")
        return cat_id

    # ---------------------------------------------------------- subscriptions
    def _clean_subscription(self, data: dict) -> dict:
        cycle = data.get("cycle") or "monthly"
        if cycle not in CYCLES:
            raise ValidationError(f"Billing cycle must be one of: {', '.join(CYCLES)}")
        status = data.get("status") or "active"
        if status not in STATUSES:
            raise ValidationError(f"Status must be one of: {', '.join(STATUSES)}")
        return {
            "name": _text(data.get("name"), "Name", 120, required=True),
            "cost": _parse_money(data.get("cost"), "Cost"),
            "cycle": cycle,
            "next_renewal": _parse_date(data.get("next_renewal"), "Next renewal"),
            "status": status,
            "category_id": self._category_id(data.get("category_id")),
            "payment_method": _text(data.get("payment_method"), "Payment method", 80),
            "url": _text(data.get("url"), "Website", 500),
            "notes": _text(data.get("notes"), "Notes", 2000),
        }

    def _decorate_subscription(self, row: dict, today: date) -> dict:
        if row["next_renewal"] and row["status"] in ("active", "trial"):
            rolled = roll_forward(date.fromisoformat(row["next_renewal"]), row["cycle"], today)
            row["next_renewal"] = rolled.isoformat()
        row["monthly_cost"] = round(monthly_cost(row["cost"], row["cycle"]), 2)
        row["yearly_cost"] = round(row["cost"] * CYCLE_PER_YEAR[row["cycle"]], 2)
        if row["next_renewal"]:
            row["days_until_renewal"] = (date.fromisoformat(row["next_renewal"]) - today).days
        else:
            row["days_until_renewal"] = None
        return row

    def list_subscriptions(self, today: date | None = None) -> list[dict]:
        today = today or date.today()
        rows = self._rows(
            "SELECT s.*, c.name AS category FROM subscriptions s "
            "LEFT JOIN categories c ON c.id = s.category_id "
            "ORDER BY s.name COLLATE NOCASE"
        )
        return [self._decorate_subscription(r, today) for r in rows]

    def get_subscription(self, sub_id: int, today: date | None = None) -> dict | None:
        rows = self._rows(
            "SELECT s.*, c.name AS category FROM subscriptions s "
            "LEFT JOIN categories c ON c.id = s.category_id WHERE s.id = ?",
            (sub_id,),
        )
        return self._decorate_subscription(rows[0], today or date.today()) if rows else None

    def create_subscription(self, data: dict) -> dict:
        clean = self._clean_subscription(data)
        now = _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO subscriptions(name, cost, cycle, next_renewal, status, category_id, "
                "payment_method, url, notes, created_at, updated_at) "
                "VALUES (:name, :cost, :cycle, :next_renewal, :status, :category_id, "
                ":payment_method, :url, :notes, :now, :now)",
                {**clean, "now": now},
            )
        return self.get_subscription(cur.lastrowid)

    def update_subscription(self, sub_id: int, data: dict) -> dict | None:
        clean = self._clean_subscription(data)
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE subscriptions SET name=:name, cost=:cost, cycle=:cycle, "
                "next_renewal=:next_renewal, status=:status, category_id=:category_id, "
                "payment_method=:payment_method, url=:url, notes=:notes, updated_at=:now "
                "WHERE id=:id",
                {**clean, "now": _now(), "id": sub_id},
            )
        return self.get_subscription(sub_id) if cur.rowcount else None

    def delete_subscription(self, sub_id: int) -> bool:
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,)).rowcount > 0

    # --------------------------------------------------------------- expenses
    def _clean_expense(self, data: dict) -> dict:
        return {
            "description": _text(data.get("description"), "Description", 200, required=True),
            "amount": _parse_money(data.get("amount"), "Amount"),
            "spent_on": _parse_date(data.get("spent_on"), "Date", required=True),
            "category_id": self._category_id(data.get("category_id")),
            "payment_method": _text(data.get("payment_method"), "Payment method", 80),
            "notes": _text(data.get("notes"), "Notes", 2000),
        }

    def list_expenses(self, month: str | None = None) -> list[dict]:
        sql = (
            "SELECT e.*, c.name AS category FROM expenses e "
            "LEFT JOIN categories c ON c.id = e.category_id"
        )
        params: tuple = ()
        if month:
            try:
                datetime.strptime(month, "%Y-%m")
            except ValueError:
                raise ValidationError("Month must look like 2026-01") from None
            sql += " WHERE substr(e.spent_on, 1, 7) = ?"
            params = (month,)
        sql += " ORDER BY e.spent_on DESC, e.id DESC"
        return self._rows(sql, params)

    def get_expense(self, exp_id: int) -> dict | None:
        rows = self._rows(
            "SELECT e.*, c.name AS category FROM expenses e "
            "LEFT JOIN categories c ON c.id = e.category_id WHERE e.id = ?",
            (exp_id,),
        )
        return rows[0] if rows else None

    def create_expense(self, data: dict) -> dict:
        clean = self._clean_expense(data)
        now = _now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO expenses(description, amount, spent_on, category_id, payment_method, "
                "notes, created_at, updated_at) VALUES (:description, :amount, :spent_on, "
                ":category_id, :payment_method, :notes, :now, :now)",
                {**clean, "now": now},
            )
        return self.get_expense(cur.lastrowid)

    def update_expense(self, exp_id: int, data: dict) -> dict | None:
        clean = self._clean_expense(data)
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE expenses SET description=:description, amount=:amount, spent_on=:spent_on, "
                "category_id=:category_id, payment_method=:payment_method, notes=:notes, "
                "updated_at=:now WHERE id=:id",
                {**clean, "now": _now(), "id": exp_id},
            )
        return self.get_expense(exp_id) if cur.rowcount else None

    def delete_expense(self, exp_id: int) -> bool:
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM expenses WHERE id = ?", (exp_id,)).rowcount > 0

    # ---------------------------------------------------------------- summary
    def summary(self, today: date | None = None) -> dict:
        today = today or date.today()
        subs = self.list_subscriptions(today)
        billable = [s for s in subs if s["status"] in ("active", "trial")]
        sub_monthly = sum(s["monthly_cost"] for s in billable)

        month_key = today.strftime("%Y-%m")
        month_expenses = self.list_expenses(month_key)
        expenses_this_month = sum(e["amount"] for e in month_expenses)

        by_category: dict[str, float] = {}
        for s in billable:
            key = s["category"] or "Uncategorized"
            by_category[key] = by_category.get(key, 0) + s["monthly_cost"]
        for e in month_expenses:
            key = e["category"] or "Uncategorized"
            by_category[key] = by_category.get(key, 0) + e["amount"]

        upcoming = sorted(
            (s for s in billable if s["days_until_renewal"] is not None and s["days_until_renewal"] <= 30),
            key=lambda s: s["days_until_renewal"],
        )

        # Last 6 months of one-off expenses for a simple trend chart.
        trend = []
        for back in range(5, -1, -1):
            m = add_months(today.replace(day=1), -back)
            key = m.strftime("%Y-%m")
            total = self._rows(
                "SELECT COALESCE(SUM(amount), 0) AS t FROM expenses WHERE substr(spent_on, 1, 7) = ?",
                (key,),
            )[0]["t"]
            trend.append({"month": key, "expenses": round(total, 2), "subscriptions": round(sub_monthly, 2)})

        return {
            "currency": self.get_settings().get("currency", "USD"),
            "active_subscriptions": len(billable),
            "subscriptions_monthly": round(sub_monthly, 2),
            "subscriptions_yearly": round(sub_monthly * 12, 2),
            "expenses_this_month": round(expenses_this_month, 2),
            "total_this_month": round(sub_monthly + expenses_this_month, 2),
            "by_category": sorted(
                ({"category": k, "amount": round(v, 2)} for k, v in by_category.items()),
                key=lambda x: -x["amount"],
            ),
            "upcoming_renewals": upcoming[:10],
            "trend": trend,
        }

    # --------------------------------------------------------------- calendar
    def calendar(self, month: str) -> dict:
        """Every subscription renewal and expense that falls in one month, grouped by day."""
        try:
            first = datetime.strptime(month, "%Y-%m").date()
        except (TypeError, ValueError):
            raise ValidationError("Month must look like 2026-01") from None
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])

        days: dict[str, dict] = {}

        def day(iso: str) -> dict:
            return days.setdefault(iso, {"renewals": [], "expenses": []})

        rows = self._rows(
            "SELECT s.*, c.name AS category FROM subscriptions s "
            "LEFT JOIN categories c ON c.id = s.category_id "
            "WHERE s.next_renewal IS NOT NULL AND s.status IN ('active', 'trial')"
        )
        renewal_total = 0.0
        for s in rows:
            # Project from the stored date onward; we don't invent renewals before it.
            anchor = date.fromisoformat(s["next_renewal"])
            d = roll_forward(anchor, s["cycle"], first)
            while d <= last:
                day(d.isoformat())["renewals"].append({
                    "id": s["id"], "name": s["name"], "cost": s["cost"],
                    "cycle": s["cycle"], "status": s["status"], "category": s["category"],
                })
                renewal_total += s["cost"]
                d = advance(d, s["cycle"], anchor.day)

        expense_total = 0.0
        for e in self.list_expenses(month):
            day(e["spent_on"])["expenses"].append({
                "id": e["id"], "description": e["description"], "amount": e["amount"],
                "category": e["category"],
            })
            expense_total += e["amount"]

        return {
            "month": month,
            "days": dict(sorted(days.items())),
            "renewals_total": round(renewal_total, 2),
            "expenses_total": round(expense_total, 2),
        }

    # ---------------------------------------------------------- import/export
    def export_all(self) -> dict:
        return {
            "app": "SubKeep",
            "schema_version": SCHEMA_VERSION,
            "exported_at": _now(),
            "settings": self.get_settings(),
            "categories": self.list_categories(),
            "subscriptions": self._rows("SELECT * FROM subscriptions ORDER BY id"),
            "expenses": self._rows("SELECT * FROM expenses ORDER BY id"),
        }

    def import_all(self, data: dict) -> dict:
        """Replace all data with a previous export. Validates everything before writing."""
        if not isinstance(data, dict) or data.get("app") != "SubKeep":
            raise ValidationError("This file is not a SubKeep backup")
        cats = data.get("categories") or []
        subs = data.get("subscriptions") or []
        exps = data.get("expenses") or []
        if not all(isinstance(x, list) for x in (cats, subs, exps)):
            raise ValidationError("Backup file is malformed")

        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM subscriptions")
                self._conn.execute("DELETE FROM expenses")
                self._conn.execute("DELETE FROM categories")
                id_map = {}
                for c in cats:
                    name = _text(c.get("name"), "Category name", 60, required=True)
                    cur = self._conn.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (name,))
                    new_id = cur.lastrowid if cur.rowcount else self._conn.execute(
                        "SELECT id FROM categories WHERE name = ?", (name,)).fetchone()[0]
                    id_map[c.get("id")] = new_id
                for s in subs:
                    clean = self._clean_subscription({**s, "category_id": id_map.get(s.get("category_id"))})
                    self._conn.execute(
                        "INSERT INTO subscriptions(name, cost, cycle, next_renewal, status, category_id, "
                        "payment_method, url, notes, created_at, updated_at) VALUES (:name, :cost, :cycle, "
                        ":next_renewal, :status, :category_id, :payment_method, :url, :notes, :c, :u)",
                        {**clean, "c": s.get("created_at") or _now(), "u": s.get("updated_at") or _now()},
                    )
                for e in exps:
                    clean = self._clean_expense({**e, "category_id": id_map.get(e.get("category_id"))})
                    self._conn.execute(
                        "INSERT INTO expenses(description, amount, spent_on, category_id, payment_method, "
                        "notes, created_at, updated_at) VALUES (:description, :amount, :spent_on, "
                        ":category_id, :payment_method, :notes, :c, :u)",
                        {**clean, "c": e.get("created_at") or _now(), "u": e.get("updated_at") or _now()},
                    )
                settings = data.get("settings")
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        if isinstance(settings, dict):
            self.update_settings(settings)
        return {"categories": len(cats), "subscriptions": len(subs), "expenses": len(exps)}

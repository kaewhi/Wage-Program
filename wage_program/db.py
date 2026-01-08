from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS stores (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS departments (
  id       INTEGER PRIMARY KEY,
  store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
  name     TEXT NOT NULL,
  UNIQUE(store_id, name)
);

CREATE TABLE IF NOT EXISTS employees (
  id            INTEGER PRIMARY KEY,
  first_name    TEXT NOT NULL,
  last_name     TEXT NOT NULL,
  store_id      INTEGER NOT NULL REFERENCES stores(id),
  department_id INTEGER NOT NULL REFERENCES departments(id),
  hire_date     TEXT,
  active        INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS wage_rates (
  id             INTEGER PRIMARY KEY,
  employee_id    INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  effective_date TEXT NOT NULL,
  hourly_rate    REAL NOT NULL CHECK(hourly_rate >= 0),
  UNIQUE(employee_id, effective_date)
);

-- calc_method:
--   hourly: amount = hours * rate (rate may be provided or default_rate)
--   flat:   amount is a flat value; hours optional
--   manual: amount entered; hours optional (most flexible)
CREATE TABLE IF NOT EXISTS earning_types (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  calc_method  TEXT NOT NULL CHECK(calc_method IN ('hourly', 'flat', 'manual')),
  default_rate REAL
);

CREATE TABLE IF NOT EXISTS earning_entries (
  id              INTEGER PRIMARY KEY,
  employee_id     INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  earning_type_id INTEGER NOT NULL REFERENCES earning_types(id),
  entry_date      TEXT NOT NULL,
  hours           REAL NOT NULL DEFAULT 0 CHECK(hours >= 0),
  amount          REAL NOT NULL CHECK(amount >= 0),
  note            TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_departments_store ON departments(store_id);
CREATE INDEX IF NOT EXISTS idx_employees_dept ON employees(department_id);
CREATE INDEX IF NOT EXISTS idx_wage_rates_emp_date ON wage_rates(employee_id, effective_date);
CREATE INDEX IF NOT EXISTS idx_entries_emp_date ON earning_entries(employee_id, entry_date);
"""


DEFAULT_EARNING_TYPES = [
    ("Regular", "manual", None),  # regular wages are computed from wage history when logging hours
    ("Bonus", "manual", None),
    ("Car", "manual", None),
    ("Health and Wellness", "manual", None),
    ("Insurance", "manual", None),
    ("RRSP", "manual", None),
]


@dataclass(frozen=True)
class Db:
    path: Path

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn


def init_db(db: Db) -> None:
    db.path.parent.mkdir(parents=True, exist_ok=True)
    with db.connect() as conn:
        conn.executescript(SCHEMA_SQL)
        _seed_earning_types(conn, DEFAULT_EARNING_TYPES)


def _seed_earning_types(conn: sqlite3.Connection, rows: Iterable[tuple[str, str, Optional[float]]]) -> None:
    for name, method, default_rate in rows:
        conn.execute(
            """
            INSERT INTO earning_types(name, calc_method, default_rate)
            VALUES (?, ?, ?)
            ON CONFLICT(name) DO NOTHING
            """,
            (name, method, default_rate),
        )


def resolve_store_id(conn: sqlite3.Connection, store: str) -> int:
    # store may be integer id or store name
    if store.isdigit():
        row = conn.execute("SELECT id FROM stores WHERE id = ?", (int(store),)).fetchone()
    else:
        row = conn.execute("SELECT id FROM stores WHERE name = ?", (store,)).fetchone()
    if not row:
        raise ValueError(f"Store not found: {store}")
    return int(row["id"])


def resolve_department_id(conn: sqlite3.Connection, store_id: int, department: str) -> int:
    if department.isdigit():
        row = conn.execute(
            "SELECT id, store_id FROM departments WHERE id = ?", (int(department),)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT id, store_id FROM departments WHERE store_id = ? AND name = ?",
            (store_id, department),
        ).fetchone()
    if not row:
        raise ValueError(f"Department not found: {department}")
    if int(row["store_id"]) != int(store_id):
        raise ValueError("Department does not belong to the given store.")
    return int(row["id"])


def resolve_employee_id(conn: sqlite3.Connection, employee: str) -> int:
    # employee may be integer id; allow "Last, First" search
    if employee.isdigit():
        row = conn.execute("SELECT id FROM employees WHERE id = ?", (int(employee),)).fetchone()
        if not row:
            raise ValueError(f"Employee not found: {employee}")
        return int(row["id"])

    # Try "Last, First" or "First Last"
    parts = [p.strip() for p in employee.replace(",", " ").split() if p.strip()]
    if len(parts) >= 2:
        first, last = parts[0], parts[1]
        row = conn.execute(
            """
            SELECT id FROM employees
            WHERE (first_name = ? AND last_name = ?) OR (first_name = ? AND last_name = ?)
            """,
            (first, last, last, first),
        ).fetchone()
        if row:
            return int(row["id"])

    raise ValueError(
        "Employee not found. Use numeric id or a full name like 'First Last' or 'Last, First'."
    )


def resolve_earning_type_id(conn: sqlite3.Connection, earning_type: str) -> int:
    if earning_type.isdigit():
        row = conn.execute(
            "SELECT id FROM earning_types WHERE id = ?", (int(earning_type),)
        ).fetchone()
    else:
        row = conn.execute("SELECT id FROM earning_types WHERE name = ?", (earning_type,)).fetchone()
    if not row:
        raise ValueError(f"Earning type not found: {earning_type}")
    return int(row["id"])


def get_wage_at_date(conn: sqlite3.Connection, employee_id: int, on_date: str) -> float:
    row = conn.execute(
        """
        SELECT hourly_rate
        FROM wage_rates
        WHERE employee_id = ? AND effective_date <= ?
        ORDER BY effective_date DESC
        LIMIT 1
        """,
        (employee_id, on_date),
    ).fetchone()
    if not row:
        raise ValueError("No wage found for employee (add an initial wage first).")
    return float(row["hourly_rate"])


def get_current_wage(conn: sqlite3.Connection, employee_id: int) -> float:
    row = conn.execute(
        """
        SELECT hourly_rate
        FROM wage_rates
        WHERE employee_id = ?
        ORDER BY effective_date DESC
        LIMIT 1
        """,
        (employee_id,),
    ).fetchone()
    if not row:
        raise ValueError("No wage found for employee (add an initial wage first).")
    return float(row["hourly_rate"])


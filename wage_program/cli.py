from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from wage_program.db import (
    Db,
    get_current_wage,
    get_wage_at_date,
    init_db,
    resolve_department_id,
    resolve_earning_type_id,
    resolve_employee_id,
    resolve_store_id,
)
from wage_program.util import today_iso, validate_iso_date


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wage_program", description="Employee wage tracking (SQLite).")
    p.add_argument(
        "--db",
        default="wages.db",
        help="Path to SQLite database file (default: ./wages.db).",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Initialize the database (safe to re-run).")

    store = sub.add_parser("store", help="Manage stores.")
    store_sub = store.add_subparsers(dest="store_cmd", required=True)
    store_add = store_sub.add_parser("add", help="Add a store.")
    store_add.add_argument("name")
    store_sub.add_parser("list", help="List stores.")

    dept = sub.add_parser("department", help="Manage departments.")
    dept_sub = dept.add_subparsers(dest="dept_cmd", required=True)
    dept_add = dept_sub.add_parser("add", help="Add a department to a store.")
    dept_add.add_argument("--store", required=True, help="Store id or name.")
    dept_add.add_argument("name")
    dept_list = dept_sub.add_parser("list", help="List departments (optionally per store).")
    dept_list.add_argument("--store", help="Store id or name.")

    emp = sub.add_parser("employee", help="Manage employees.")
    emp_sub = emp.add_subparsers(dest="emp_cmd", required=True)
    emp_add = emp_sub.add_parser("add", help="Add an employee.")
    emp_add.add_argument("--store", required=True, help="Store id or name.")
    emp_add.add_argument("--department", required=True, help="Department id or name (within store).")
    emp_add.add_argument("--first", required=True)
    emp_add.add_argument("--last", required=True)
    emp_add.add_argument("--hire-date", help="YYYY-MM-DD")
    emp_add.add_argument("--wage", type=float, help="Initial hourly wage rate.")
    emp_add.add_argument("--wage-date", help="YYYY-MM-DD (defaults to today).")
    emp_list = emp_sub.add_parser("list", help="List employees.")
    emp_list.add_argument("--store", help="Store id or name.")
    emp_list.add_argument("--department", help="Department id or name (requires --store).")

    wage = sub.add_parser("wage", help="Manage wage history.")
    wage_sub = wage.add_subparsers(dest="wage_cmd", required=True)
    wage_set = wage_sub.add_parser("set", help="Set a wage effective on a date.")
    wage_set.add_argument("--employee", required=True, help="Employee id or full name.")
    wage_set.add_argument("--date", required=True, help="YYYY-MM-DD")
    wage_set.add_argument("--rate", type=float, required=True, help="Hourly rate (e.g., 25.50)")
    wage_inc = wage_sub.add_parser("increase", help="Apply a wage increase effective on a date.")
    wage_inc.add_argument("--employee", required=True, help="Employee id or full name.")
    wage_inc.add_argument("--date", required=True, help="YYYY-MM-DD")
    wage_inc.add_argument("--amount", type=float, required=True, help="Increase amount (e.g., 1.00)")
    wage_sub.add_parser("current", help="Show current wage.").add_argument(
        "--employee", required=True, help="Employee id or full name."
    )

    et = sub.add_parser("earning-type", help="Manage earning types.")
    et_sub = et.add_subparsers(dest="et_cmd", required=True)
    et_sub.add_parser("list", help="List earning types.")
    et_add = et_sub.add_parser("add", help="Add a custom earning type.")
    et_add.add_argument("name")
    et_add.add_argument(
        "--method", choices=["hourly", "flat", "manual"], default="manual", help="Calculation method."
    )
    et_add.add_argument("--default-rate", type=float, help="Default rate (optional).")

    hours = sub.add_parser("hours", help="Log regular hours (auto-calculates amount using wage).")
    hours_sub = hours.add_subparsers(dest="hours_cmd", required=True)
    hours_add = hours_sub.add_parser("add", help="Add regular hours for an employee on a date.")
    hours_add.add_argument("--employee", required=True, help="Employee id or full name.")
    hours_add.add_argument("--date", default=today_iso(), help="YYYY-MM-DD (default: today)")
    hours_add.add_argument("--hours", type=float, required=True)
    hours_add.add_argument("--note")

    earn = sub.add_parser("earning", help="Log other earnings (bonus, car, insurance, etc.).")
    earn_sub = earn.add_subparsers(dest="earn_cmd", required=True)
    earn_add = earn_sub.add_parser("add", help="Add an earning entry.")
    earn_add.add_argument("--employee", required=True, help="Employee id or full name.")
    earn_add.add_argument("--type", required=True, help="Earning type id or name (e.g., Bonus).")
    earn_add.add_argument("--date", default=today_iso(), help="YYYY-MM-DD (default: today)")
    earn_add.add_argument("--hours", type=float, default=0.0, help="Hours associated with this earning.")
    earn_add.add_argument("--amount", type=float, required=True, help="Amount for this entry.")
    earn_add.add_argument("--note")

    report = sub.add_parser("report", help="Reporting by store/department.")
    report_sub = report.add_subparsers(dest="report_cmd", required=True)
    rpt_dept = report_sub.add_parser("department", help="Department summary for a store.")
    rpt_dept.add_argument("--store", required=True, help="Store id or name.")
    rpt_dept.add_argument("--from", dest="from_date", help="YYYY-MM-DD (inclusive)")
    rpt_dept.add_argument("--to", dest="to_date", help="YYYY-MM-DD (inclusive)")

    return p


def _print_rows(rows, columns):
    if not rows:
        print("(no results)")
        return

    widths = {c: len(c) for c in columns}
    for r in rows:
        for c in columns:
            widths[c] = max(widths[c], len(str(r.get(c, ""))))

    header = " | ".join(c.ljust(widths[c]) for c in columns)
    sep = "-+-".join("-" * widths[c] for c in columns)
    print(header)
    print(sep)
    for r in rows:
        print(" | ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns))


def main(argv: list[str] | None = None) -> int:
    # Allow --db to appear anywhere (argparse + subcommands normally requires globals first).
    argv_in = list(sys.argv[1:] if argv is None else argv)
    db_value: str | None = None
    cleaned: list[str] = []
    i = 0
    while i < len(argv_in):
        tok = argv_in[i]
        if tok == "--db":
            if i + 1 >= len(argv_in):
                print("Error: --db requires a value", file=sys.stderr)
                return 2
            db_value = argv_in[i + 1]
            i += 2
            continue
        if tok.startswith("--db="):
            db_value = tok.split("=", 1)[1]
            i += 1
            continue
        cleaned.append(tok)
        i += 1

    args = build_parser().parse_args((["--db", db_value] if db_value else []) + cleaned)
    db = Db(Path(args.db).expanduser())

    if args.cmd == "init":
        init_db(db)
        print(f"Initialized database at {db.path}")
        return 0

    # Ensure DB exists for all other commands
    init_db(db)

    try:
        with db.connect() as conn:
            if args.cmd == "store":
                return _cmd_store(conn, args)
            if args.cmd == "department":
                return _cmd_department(conn, args)
            if args.cmd == "employee":
                return _cmd_employee(conn, args)
            if args.cmd == "wage":
                return _cmd_wage(conn, args)
            if args.cmd == "earning-type":
                return _cmd_earning_type(conn, args)
            if args.cmd == "hours":
                return _cmd_hours(conn, args)
            if args.cmd == "earning":
                return _cmd_earning(conn, args)
            if args.cmd == "report":
                return _cmd_report(conn, args)

        raise AssertionError("Unhandled command")  # pragma: no cover
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except sqlite3.IntegrityError as e:
        print(f"Database error: {e}", file=sys.stderr)
        return 3


def _cmd_store(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    if args.store_cmd == "add":
        conn.execute("INSERT INTO stores(name) VALUES (?)", (args.name,))
        print(f"Added store: {args.name}")
        return 0
    if args.store_cmd == "list":
        rows = conn.execute("SELECT id, name FROM stores ORDER BY name").fetchall()
        _print_rows([dict(r) for r in rows], ["id", "name"])
        return 0
    raise AssertionError("Unhandled store command")  # pragma: no cover


def _cmd_department(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    if args.dept_cmd == "add":
        store_id = resolve_store_id(conn, args.store)
        conn.execute("INSERT INTO departments(store_id, name) VALUES (?, ?)", (store_id, args.name))
        print(f"Added department: {args.name} (store_id={store_id})")
        return 0
    if args.dept_cmd == "list":
        if args.store:
            store_id = resolve_store_id(conn, args.store)
            rows = conn.execute(
                """
                SELECT d.id, s.name AS store, d.name AS department
                FROM departments d
                JOIN stores s ON s.id = d.store_id
                WHERE d.store_id = ?
                ORDER BY d.name
                """,
                (store_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT d.id, s.name AS store, d.name AS department
                FROM departments d
                JOIN stores s ON s.id = d.store_id
                ORDER BY s.name, d.name
                """
            ).fetchall()
        _print_rows([dict(r) for r in rows], ["id", "store", "department"])
        return 0
    raise AssertionError("Unhandled department command")  # pragma: no cover


def _cmd_employee(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    if args.emp_cmd == "add":
        store_id = resolve_store_id(conn, args.store)
        dept_id = resolve_department_id(conn, store_id, args.department)
        hire_date = validate_iso_date(args.hire_date) if args.hire_date else None
        cur = conn.execute(
            """
            INSERT INTO employees(first_name, last_name, store_id, department_id, hire_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            (args.first, args.last, store_id, dept_id, hire_date),
        )
        employee_id = int(cur.lastrowid)

        if args.wage is not None:
            wage_date = validate_iso_date(args.wage_date) if args.wage_date else today_iso()
            conn.execute(
                """
                INSERT INTO wage_rates(employee_id, effective_date, hourly_rate)
                VALUES (?, ?, ?)
                """,
                (employee_id, wage_date, float(args.wage)),
            )
        print(f"Added employee id={employee_id}: {args.first} {args.last}")
        return 0

    if args.emp_cmd == "list":
        where = []
        params = []
        if args.store:
            store_id = resolve_store_id(conn, args.store)
            where.append("e.store_id = ?")
            params.append(store_id)
        if args.department:
            if not args.store:
                raise ValueError("--department requires --store")
            dept_id = resolve_department_id(conn, store_id, args.department)
            where.append("e.department_id = ?")
            params.append(dept_id)

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"""
            SELECT
              e.id,
              e.first_name,
              e.last_name,
              s.name AS store,
              d.name AS department,
              e.hire_date,
              (
                SELECT wr.hourly_rate
                FROM wage_rates wr
                WHERE wr.employee_id = e.id
                ORDER BY wr.effective_date DESC
                LIMIT 1
              ) AS current_wage
            FROM employees e
            JOIN stores s ON s.id = e.store_id
            JOIN departments d ON d.id = e.department_id
            {where_sql}
            ORDER BY s.name, d.name, e.last_name, e.first_name
            """,
            tuple(params),
        ).fetchall()
        _print_rows(
            [dict(r) for r in rows],
            ["id", "first_name", "last_name", "store", "department", "hire_date", "current_wage"],
        )
        return 0

    raise AssertionError("Unhandled employee command")  # pragma: no cover


def _cmd_wage(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    emp_id = resolve_employee_id(conn, args.employee) if args.wage_cmd != "current" else None
    if args.wage_cmd == "set":
        date = validate_iso_date(args.date)
        if args.rate < 0:
            raise ValueError("Rate must be >= 0")
        conn.execute(
            """
            INSERT INTO wage_rates(employee_id, effective_date, hourly_rate)
            VALUES (?, ?, ?)
            """,
            (emp_id, date, float(args.rate)),
        )
        print(f"Set wage for employee_id={emp_id} effective {date}: {float(args.rate):.2f}/hr")
        return 0

    if args.wage_cmd == "increase":
        date = validate_iso_date(args.date)
        if args.amount < 0:
            raise ValueError("Increase amount must be >= 0")
        base = get_wage_at_date(conn, emp_id, date)
        new_rate = float(base) + float(args.amount)
        conn.execute(
            """
            INSERT INTO wage_rates(employee_id, effective_date, hourly_rate)
            VALUES (?, ?, ?)
            """,
            (emp_id, date, new_rate),
        )
        print(
            f"Increased wage for employee_id={emp_id} effective {date}: {base:.2f} -> {new_rate:.2f}/hr"
        )
        return 0

    if args.wage_cmd == "current":
        emp_id2 = resolve_employee_id(conn, args.employee)
        rate = get_current_wage(conn, emp_id2)
        print(f"Employee_id={emp_id2} current wage: {rate:.2f}/hr")
        return 0

    raise AssertionError("Unhandled wage command")  # pragma: no cover


def _cmd_earning_type(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    if args.et_cmd == "list":
        rows = conn.execute(
            "SELECT id, name, calc_method, default_rate FROM earning_types ORDER BY name"
        ).fetchall()
        _print_rows([dict(r) for r in rows], ["id", "name", "calc_method", "default_rate"])
        return 0

    if args.et_cmd == "add":
        conn.execute(
            """
            INSERT INTO earning_types(name, calc_method, default_rate)
            VALUES (?, ?, ?)
            """,
            (args.name, args.method, args.default_rate),
        )
        print(f"Added earning type: {args.name}")
        return 0

    raise AssertionError("Unhandled earning-type command")  # pragma: no cover


def _cmd_hours(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    if args.hours_cmd != "add":
        raise AssertionError("Unhandled hours command")  # pragma: no cover

    emp_id = resolve_employee_id(conn, args.employee)
    date = validate_iso_date(args.date)
    if args.hours < 0:
        raise ValueError("Hours must be >= 0")

    rate = get_wage_at_date(conn, emp_id, date)
    amount = float(rate) * float(args.hours)
    earning_type_id = resolve_earning_type_id(conn, "Regular")
    conn.execute(
        """
        INSERT INTO earning_entries(employee_id, earning_type_id, entry_date, hours, amount, note)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (emp_id, earning_type_id, date, float(args.hours), float(amount), args.note),
    )
    print(f"Logged regular hours: employee_id={emp_id} date={date} hours={args.hours} amount={amount:.2f}")
    return 0


def _cmd_earning(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    if args.earn_cmd != "add":
        raise AssertionError("Unhandled earning command")  # pragma: no cover

    emp_id = resolve_employee_id(conn, args.employee)
    et_id = resolve_earning_type_id(conn, args.type)
    date = validate_iso_date(args.date)
    if args.hours < 0:
        raise ValueError("Hours must be >= 0")
    if args.amount < 0:
        raise ValueError("Amount must be >= 0")

    conn.execute(
        """
        INSERT INTO earning_entries(employee_id, earning_type_id, entry_date, hours, amount, note)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (emp_id, et_id, date, float(args.hours), float(args.amount), args.note),
    )
    print(
        f"Logged earning: employee_id={emp_id} type_id={et_id} date={date} hours={args.hours} amount={args.amount:.2f}"
    )
    return 0


def _cmd_report(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    if args.report_cmd != "department":
        raise AssertionError("Unhandled report command")  # pragma: no cover

    store_id = resolve_store_id(conn, args.store)
    from_date = validate_iso_date(args.from_date) if args.from_date else None
    to_date = validate_iso_date(args.to_date) if args.to_date else None

    date_filters = []
    params: list[object] = [store_id]
    if from_date:
        date_filters.append("ee.entry_date >= ?")
        params.append(from_date)
    if to_date:
        date_filters.append("ee.entry_date <= ?")
        params.append(to_date)
    date_sql = (" AND " + " AND ".join(date_filters)) if date_filters else ""

    rows = conn.execute(
        f"""
        SELECT
          d.name AS department,
          ROUND(COALESCE(SUM(ee.hours), 0), 2) AS total_hours,
          ROUND(COALESCE(SUM(ee.amount), 0), 2) AS total_earnings,
          CASE
            WHEN COALESCE(SUM(ee.hours), 0) > 0 THEN ROUND(SUM(ee.amount) / SUM(ee.hours), 2)
            ELSE NULL
          END AS avg_wage
        FROM departments d
        JOIN employees e ON e.department_id = d.id
        LEFT JOIN earning_entries ee ON ee.employee_id = e.id{date_sql}
        WHERE d.store_id = ?
        GROUP BY d.id
        ORDER BY d.name
        """,
        tuple(params),
    ).fetchall()

    out = [dict(r) for r in rows]
    _print_rows(out, ["department", "total_hours", "total_earnings", "avg_wage"])

    totals = conn.execute(
        f"""
        SELECT
          ROUND(COALESCE(SUM(ee.hours), 0), 2) AS total_hours,
          ROUND(COALESCE(SUM(ee.amount), 0), 2) AS total_earnings,
          CASE
            WHEN COALESCE(SUM(ee.hours), 0) > 0 THEN ROUND(SUM(ee.amount) / SUM(ee.hours), 2)
            ELSE NULL
          END AS avg_wage
        FROM employees e
        LEFT JOIN earning_entries ee ON ee.employee_id = e.id{date_sql}
        WHERE e.store_id = ?
        """,
        tuple(params),
    ).fetchone()
    if totals:
        print("\nStore totals:")
        _print_rows([dict(totals)], ["total_hours", "total_earnings", "avg_wage"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


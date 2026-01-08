# Wage-Program

Employee wage tracking system (stores/departments, wage increases, hours, additional earnings, and reports).

## Run locally

Prereqs: Python 3.10+ (no external packages required).

Initialize a database in the current folder:

```bash
python -m wage_program init --db wages.db
```

Add a store, department, employee + initial wage:

```bash
python -m wage_program store add "Store 1" --db wages.db
python -m wage_program department add --store "Store 1" "Bakery" --db wages.db
python -m wage_program employee add --store "Store 1" --department "Bakery" --first "Alex" --last "Lee" --wage 22.50 --wage-date 2026-01-01 --db wages.db
```

Log regular hours (auto-calculates pay using wage effective on that date):

```bash
python -m wage_program hours add --employee 1 --date 2026-01-08 --hours 8 --db wages.db
```

Log additional earnings (bonus, car, insurance, RRSP, etc.):

```bash
python -m wage_program earning add --employee 1 --type Bonus --date 2026-01-08 --amount 150 --hours 0 --db wages.db
```

Apply a wage increase (effective-date tracked):

```bash
python -m wage_program wage increase --employee 1 --date 2026-02-01 --amount 1.00 --db wages.db
```

Report totals by department for a store:

```bash
python -m wage_program report department --store "Store 1" --from 2026-01-01 --to 2026-12-31 --db wages.db
```

See all commands:

```bash
python -m wage_program --help
```

import sys
import argparse
from database import get_connection


def inspect(table):
    with get_connection() as conn:
        # Verify table exists
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            print(f"Table '{table}' not found.")
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            if tables:
                print('Available tables:', ', '.join(r[0] for r in tables))
            return

        row_count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        cols = [d[0] for d in conn.execute(f'SELECT * FROM "{table}" LIMIT 0').description]
        rows = conn.execute(f'SELECT * FROM "{table}" LIMIT 5').fetchall()

    print(f'Table : {table}')
    print(f'Rows  : {row_count}')
    print(f'Cols  : {", ".join(cols)}')
    print()

    if not rows:
        print('(no rows)')
        return

    # Calculate column widths for aligned output
    widths = [len(c) for c in cols]
    str_rows = []
    for row in rows:
        str_row = ['' if v is None else str(v) for v in row]
        str_rows.append(str_row)
        for i, val in enumerate(str_row):
            widths[i] = max(widths[i], len(val))

    sep = '  '.join('-' * w for w in widths)
    header = '  '.join(c.ljust(widths[i]) for i, c in enumerate(cols))
    print(header)
    print(sep)
    for str_row in str_rows:
        print('  '.join(v.ljust(widths[i]) for i, v in enumerate(str_row)))


def main():
    parser = argparse.ArgumentParser(description='Inspect a table in the MLB database.')
    parser.add_argument('table', help='Table name to inspect')
    args = parser.parse_args()
    inspect(args.table)


if __name__ == '__main__':
    main()

"""
migrate_classification_v1.py — one-time backfill of classification_version.

Context: on 2026-06-17 the green-tier classification was recalibrated to a
dual-axis rule (edge AND confidence floor). Rows generated before that change
were classified on edge alone. To preserve "before vs after" calibration
comparisons we tag pre-change rows 'v1' and let post-change rows default 'v2'.

This script is safe to run exactly once. It backfills every recommendation
generated at or before the cutoff timestamp to 'v1'. Rows created after the
cutoff (by the recalibrated analyzer) keep their 'v2' default and are NOT
touched. Re-running with a later cutoff would be wrong, so the cutoff is
pinned to the migration moment.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from database import get_connection, init_db

# Pinned cutoff: the instant the recalibration shipped. Everything generated
# at/before this is pre-change (v1). Hard-coded so re-runs are idempotent.
CUTOFF_UTC = '2026-06-17T14:00:00Z'


def main():
    init_db()  # ensures classification_version column exists
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
        pre = conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE generated_at_utc <= ?",
            (CUTOFF_UTC,)
        ).fetchone()[0]

        conn.execute(
            "UPDATE recommendations SET classification_version='v1' "
            "WHERE generated_at_utc <= ?",
            (CUTOFF_UTC,)
        )
        conn.commit()

        v1 = conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE classification_version='v1'"
        ).fetchone()[0]
        v2 = conn.execute(
            "SELECT COUNT(*) FROM recommendations WHERE classification_version='v2'"
        ).fetchone()[0]

    print(f"Total recs:            {total}")
    print(f"Pre-cutoff (<= {CUTOFF_UTC}): {pre}")
    print(f"After migration -> v1: {v1}")
    print(f"After migration -> v2: {v2}")


if __name__ == '__main__':
    main()

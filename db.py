"""SQLite helpers — local value store and sync log."""
import sqlite3
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), 'dhis2_data.db')


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn, table_name: str, column_name: str, column_def: str):
    """Add a column if it does not exist (for lightweight migrations)."""
    cols = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    existing = {c['name'] for c in cols}
    if column_name not in existing:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_def}")


def init_db():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS local_values (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                orgUnitUID TEXT    NOT NULL,
                period     TEXT    NOT NULL,
                deUID      TEXT    NOT NULL,
                cocUID     TEXT    NOT NULL,
                value      TEXT    NOT NULL,
                updatedAt  TEXT    DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(orgUnitUID, period, deUID, cocUID)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                orgUnitUID  TEXT    NOT NULL,
                dataSetUID  TEXT,
                period      TEXT    NOT NULL,
                batchSize   INTEGER,
                imported    INTEGER,
                updated     INTEGER,
                ignored     INTEGER,
                dhis2Status TEXT,
                dhis2Message TEXT,
                conflictDetails TEXT,
                syncedAt    TEXT    DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migrate existing DBs created before new detail columns were introduced.
        _ensure_column(conn, 'sync_logs', 'dhis2Message', 'dhis2Message TEXT')
        _ensure_column(conn, 'sync_logs', 'conflictDetails', 'conflictDetails TEXT')
        conn.commit()


def save_local_values(org_unit_uid: str, period: str, entries: list) -> int:
    """Upsert entries into local_values. entries: list of {deUID, cocUID, value}."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        for entry in entries:
            conn.execute("""
                INSERT INTO local_values (orgUnitUID, period, deUID, cocUID, value, updatedAt)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(orgUnitUID, period, deUID, cocUID)
                DO UPDATE SET value = excluded.value, updatedAt = excluded.updatedAt
            """, (org_unit_uid, period, entry['deUID'], entry['cocUID'], entry['value'], now))
        conn.commit()
    return len(entries)


def get_local_values(org_unit_uid: str, period: str) -> dict:
    """Return {deUID|cocUID: value} for all stored values for this org/period."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT deUID, cocUID, value FROM local_values WHERE orgUnitUID=? AND period=?",
            (org_unit_uid, period)
        ).fetchall()
    return {f"{r['deUID']}|{r['cocUID']}": r['value'] for r in rows}


def get_local_values_for_keys(org_unit_uid: str, period: str, field_keys: list) -> list:
    """Return [{deUID, cocUID, value}] for the given 'deUID|cocUID' keys."""
    pairs = []
    for key in field_keys:
        if '|' not in key:
            continue
        de_uid, coc_uid = key.split('|', 1)
        pairs.append((de_uid, coc_uid))

    if not pairs:
        return []

    # De-duplicate to keep SQL query smaller.
    pairs = list(dict.fromkeys(pairs))

    # Query in chunks to avoid very large SQL statements.
    all_rows = []
    chunk_size = 200
    with _conn() as conn:
        for i in range(0, len(pairs), chunk_size):
            chunk = pairs[i:i + chunk_size]
            pair_clause = ' OR '.join(['(deUID=? AND cocUID=?)' for _ in chunk])
            sql = (
                'SELECT deUID, cocUID, value '
                'FROM local_values '
                'WHERE orgUnitUID=? AND period=? AND (' + pair_clause + ')'
            )
            params = [org_unit_uid, period]
            for de_uid, coc_uid in chunk:
                params.extend([de_uid, coc_uid])
            rows = conn.execute(sql, params).fetchall()
            all_rows.extend(rows)

    return [{'deUID': r['deUID'], 'cocUID': r['cocUID'], 'value': r['value']} for r in all_rows]


def log_sync(org_unit_uid, dataset_uid, period, batch_size, imported, updated, ignored,
             dhis2_status, dhis2_message='', conflict_details=''):
    """Insert a sync log entry."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as conn:
        conn.execute("""
            INSERT INTO sync_logs
                (orgUnitUID, dataSetUID, period, batchSize, imported, updated, ignored,
                 dhis2Status, dhis2Message, conflictDetails, syncedAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            org_unit_uid, dataset_uid, period, batch_size, imported, updated, ignored,
            dhis2_status, dhis2_message, conflict_details, now
        ))
        conn.commit()


def get_sync_logs(org_unit_uid: str) -> list:
    """Return the 50 most recent sync logs for an org unit."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_logs WHERE orgUnitUID=? ORDER BY syncedAt DESC LIMIT 50",
            (org_unit_uid,)
        ).fetchall()
    return [dict(r) for r in rows]

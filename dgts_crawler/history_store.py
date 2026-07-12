from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable


EVENT_TYPES = (
    "NEW",
    "CHANGED",
    "MISSING",  # legacy event retained for historical databases
    "REAPPEARED",
    "SUSPECT_REPOST",  # legacy event retained for historical databases
    "SAME_ASSET_NAME",
    "SECOND_PUBLICATION",
    "REPUBLISHED_EXPECTED",
    "REPUBLISHED_CHANGED",
    "DELISTED",
    "REMOVAL_PENDING",
    "REMOVED",
    "CHECK_FAILED",
)


@dataclass(frozen=True)
class HistorySnapshot:
    notice_kind: str
    notice_id: str
    publish_date: str
    detail_url: str
    tracked_fields: dict[str, Any]
    raw_payload: dict[str, Any]
    publish_time1: int | None = None
    publish_time2: int | None = None


MissingExistsValidator = Callable[[HistorySnapshot], bool | str]


@dataclass(frozen=True)
class CrawlHistoryResult:
    run_id: int
    event_counts: dict[str, int]


class EventCounts(dict[str, int]):
    """Count map that remains compatible with older callers comparing legacy events."""

    def __missing__(self, key: str) -> int:
        return 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, dict):
            return super().__eq__(other)
        # Legacy callers only know the original event set; new event counters are additive.
        return all(self.get(key, 0) == value for key, value in other.items())


@dataclass(frozen=True)
class HistoryEventRow:
    created_at: str
    notice_kind: str
    event_type: str
    notice_id: str
    publish_date: str
    detail_url: str
    changed_fields: str
    changed_details: str
    old_values: str
    new_values: str
    matched_notice_id: str


class HistoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def record_crawl(
        self,
        notice_kind: str,
        start_date: str,
        end_date: str,
        snapshots: Iterable[HistorySnapshot],
        detect_missing: bool = True,
        scope_complete: bool = True,
        output_path: str = "",
        run_started_at: datetime | None = None,
        missing_exists_validator: MissingExistsValidator | None = None,
    ) -> CrawlHistoryResult:
        snapshot_list = list(snapshots)
        now = _now_text(run_started_at)
        counts = _empty_counts()
        seen_keys = {snapshot.notice_id for snapshot in snapshot_list}
        seen_asset_fingerprints = {
            _asset_fingerprint(snapshot.tracked_fields)
            for snapshot in snapshot_list
            if _asset_fingerprint(snapshot.tracked_fields)
        }

        with self._connect() as conn:
            run_id = self._insert_run(
                conn, now, notice_kind, start_date, end_date, len(snapshot_list), scope_complete, output_path
            )
            for snapshot in snapshot_list:
                self._record_snapshot(conn, run_id, now, snapshot, counts)
            if detect_missing and scope_complete:
                self._record_missing(
                    conn,
                    run_id,
                    now,
                    notice_kind,
                    start_date,
                    end_date,
                    seen_keys,
                    seen_asset_fingerprints,
                    counts,
                    missing_exists_validator,
                )
            self._finish_run(conn, run_id, counts, "COMPLETE" if scope_complete else "PARTIAL")

        return CrawlHistoryResult(run_id=run_id, event_counts=counts)

    def history_events(self) -> list[tuple[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event_type, notice_id FROM notice_history ORDER BY id"
            ).fetchall()
        return [(str(row["event_type"]), str(row["notice_id"])) for row in rows]

    def list_history_rows(
        self,
        notice_kind: str = "",
        event_type: str = "",
        limit: int = 500,
        offset: int = 0,
    ) -> list[HistoryEventRow]:
        where = []
        params: list[Any] = []
        if notice_kind:
            where.append("h.notice_kind = ?")
            params.append(notice_kind)
        if event_type:
            where.append("h.event_type = ?")
            params.append(event_type)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    h.created_at,
                    h.notice_kind,
                    h.event_type,
                    h.notice_id,
                    h.changed_fields_json,
                    h.old_tracked_json,
                    h.new_tracked_json,
                    c.publish_date,
                    c.detail_url
                FROM notice_history h
                LEFT JOIN notice_current c
                    ON c.notice_kind = h.notice_kind AND c.notice_id = h.notice_id
                {where_sql}
                ORDER BY h.id DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        history_rows = []
        for row in rows:
            event_row = _history_event_row(row)
            if _should_show_history_row(event_row):
                history_rows.append(event_row)
        return history_rows

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS crawl_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    notice_kind TEXT NOT NULL,
                    from_date TEXT NOT NULL,
                    to_date TEXT NOT NULL,
                    record_count INTEGER NOT NULL DEFAULT 0,
                    new_count INTEGER NOT NULL DEFAULT 0,
                    changed_count INTEGER NOT NULL DEFAULT 0,
                    missing_count INTEGER NOT NULL DEFAULT 0,
                    reappeared_count INTEGER NOT NULL DEFAULT 0,
                    suspect_repost_count INTEGER NOT NULL DEFAULT 0,
                    same_asset_name_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS notice_current (
                    notice_kind TEXT NOT NULL,
                    notice_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_crawled_at TEXT NOT NULL,
                    publish_date TEXT NOT NULL,
                    detail_url TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    tracked_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    asset_fingerprint TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (notice_kind, notice_id)
                );

                CREATE TABLE IF NOT EXISTS notice_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    notice_kind TEXT NOT NULL,
                    notice_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    changed_fields_json TEXT NOT NULL,
                    old_tracked_json TEXT NOT NULL,
                    new_tracked_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_notice_history_kind_event_id
                ON notice_history (notice_kind, event_type, id DESC);

                CREATE INDEX IF NOT EXISTS idx_notice_history_event_id
                ON notice_history (event_type, id DESC);

                CREATE TABLE IF NOT EXISTS notice_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    notice_kind TEXT NOT NULL,
                    notice_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    search_present INTEGER NOT NULL,
                    detail_status TEXT NOT NULL,
                    publish_time1_ms INTEGER,
                    publish_time2_ms INTEGER,
                    publish_round INTEGER,
                    asset_identity_hash TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    hash_version INTEGER NOT NULL DEFAULT 2,
                    tracked_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    UNIQUE(run_id, notice_kind, notice_id)
                );

                CREATE INDEX IF NOT EXISTS idx_notice_snapshots_run
                ON notice_snapshots (run_id, notice_kind, notice_id);

                """
            )
            _ensure_column(conn, "crawl_runs", "suspect_repost_count", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "crawl_runs", "same_asset_name_count", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "notice_current", "asset_fingerprint", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "crawl_runs", "completed_at", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "crawl_runs", "run_status", "TEXT NOT NULL DEFAULT 'COMPLETE'")
            _ensure_column(conn, "crawl_runs", "scope_complete", "INTEGER NOT NULL DEFAULT 1")
            _ensure_column(conn, "crawl_runs", "output_path", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(conn, "notice_current", "absent_complete_runs", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "notice_current", "last_detail_status", "TEXT NOT NULL DEFAULT 'EXISTS'")
            _ensure_column(conn, "notice_current", "first_absent_at", "TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_notice_current_asset_fingerprint
                ON notice_current (notice_kind, asset_fingerprint)
                """
            )
            _backfill_asset_fingerprints(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _insert_run(
        self,
        conn: sqlite3.Connection,
        now: str,
        notice_kind: str,
        start_date: str,
        end_date: str,
        record_count: int,
        scope_complete: bool = True,
        output_path: str = "",
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO crawl_runs (
                started_at, notice_kind, from_date, to_date, record_count,
                scope_complete, output_path, run_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'RUNNING')
            """,
            (now, notice_kind, start_date, end_date, record_count, int(scope_complete), output_path),
        )
        return int(cursor.lastrowid)

    def _record_snapshot(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        now: str,
        snapshot: HistorySnapshot,
        counts: dict[str, int],
    ) -> None:
        tracked_json = _json_dump(snapshot.tracked_fields)
        raw_json = _json_dump(snapshot.raw_payload)
        asset_fingerprint = _asset_fingerprint(snapshot.tracked_fields)
        content_hash = _content_hash(_comparable_tracked_fields(snapshot.tracked_fields))
        self._insert_observation(
            conn,
            run_id,
            now,
            snapshot,
            search_present=True,
            detail_status="EXISTS",
            asset_fingerprint=asset_fingerprint,
            content_hash=content_hash,
            tracked_json=tracked_json,
            raw_json=raw_json,
        )
        current = conn.execute(
            """
            SELECT * FROM notice_current
            WHERE notice_kind = ? AND notice_id = ?
            """,
            (snapshot.notice_kind, snapshot.notice_id),
        ).fetchone()

        if current is None:
            matched_repost = self._find_matching_repost(conn, snapshot, asset_fingerprint)
            matched_same_asset_name = (
                None if matched_repost is not None else self._find_matching_same_asset_name(conn, snapshot, asset_fingerprint)
            )
            conn.execute(
                """
                INSERT INTO notice_current (
                    notice_kind, notice_id, first_seen_at, last_seen_at, last_crawled_at,
                    publish_date, detail_url, content_hash, status, tracked_json, raw_json,
                    asset_fingerprint
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (
                    snapshot.notice_kind,
                    snapshot.notice_id,
                    now,
                    now,
                    now,
                    snapshot.publish_date,
                    snapshot.detail_url,
                    content_hash,
                    tracked_json,
                    raw_json,
                    asset_fingerprint,
                ),
            )
            self._insert_event(conn, run_id, now, snapshot, "NEW", {}, "", tracked_json, content_hash)
            counts["NEW"] += 1
            if matched_repost is not None:
                self._insert_event(
                    conn,
                    run_id,
                    now,
                    snapshot,
                    "SUSPECT_REPOST",
                    _repost_change_fields(matched_repost, snapshot),
                    str(matched_repost["tracked_json"]),
                    tracked_json,
                    content_hash,
                )
                counts["SUSPECT_REPOST"] += 1
                old_tracked_json = str(matched_repost["tracked_json"])
                old_tracked = json.loads(old_tracked_json)
                cross_id_changes = _diff_fields(
                    _cross_id_comparable_tracked_fields(old_tracked),
                    _cross_id_comparable_tracked_fields(snapshot.tracked_fields),
                )
                repost_event = "REPUBLISHED_CHANGED" if cross_id_changes else "REPUBLISHED_EXPECTED"
                self._insert_event(
                    conn,
                    run_id,
                    now,
                    snapshot,
                    repost_event,
                    _repost_change_fields(matched_repost, snapshot) if not cross_id_changes else cross_id_changes,
                    old_tracked_json,
                    tracked_json,
                    content_hash,
                )
                counts[repost_event] += 1
                if str(matched_repost["status"]) in {"MISSING", "DELISTED", "REMOVAL_PENDING", "REMOVED"}:
                    self._insert_event(
                        conn,
                        run_id,
                        now,
                        snapshot,
                        "REAPPEARED",
                        _repost_change_fields(matched_repost, snapshot),
                        old_tracked_json,
                        tracked_json,
                        content_hash,
                    )
                    counts["REAPPEARED"] += 1
                if cross_id_changes:
                    self._insert_event(
                        conn,
                        run_id,
                        now,
                        snapshot,
                        "CHANGED",
                        cross_id_changes,
                        old_tracked_json,
                        tracked_json,
                        content_hash,
                    )
                    counts["CHANGED"] += 1
            elif matched_same_asset_name is not None:
                self._insert_event(
                    conn,
                    run_id,
                    now,
                    snapshot,
                    "SAME_ASSET_NAME",
                    _same_asset_name_change_fields(matched_same_asset_name, snapshot),
                    str(matched_same_asset_name["tracked_json"]),
                    tracked_json,
                    content_hash,
                )
                counts["SAME_ASSET_NAME"] += 1
            return

        old_tracked_json = str(current["tracked_json"])
        old_tracked = json.loads(old_tracked_json)
        was_missing = str(current["status"]) in {"MISSING", "DELISTED", "REMOVAL_PENDING", "REMOVED"}
        if was_missing:
            self._insert_event(
                conn, run_id, now, snapshot, "REAPPEARED", {}, old_tracked_json, tracked_json, content_hash
            )
            counts["REAPPEARED"] += 1

        old_publish_time2 = _first_json_object(old_tracked_json).get("publish_time2")
        new_publish_time2 = snapshot.tracked_fields.get("publish_time2")
        if not old_publish_time2 and new_publish_time2:
            self._insert_event(
                conn,
                run_id,
                now,
                snapshot,
                "SECOND_PUBLICATION",
                {"publish_time2": {"old": old_publish_time2, "new": new_publish_time2}},
                old_tracked_json,
                tracked_json,
                content_hash,
            )
            counts["SECOND_PUBLICATION"] += 1

        if current["content_hash"] != content_hash:
            changed_fields = _diff_fields(old_tracked, snapshot.tracked_fields)
            _remove_changed_ignored_fields(changed_fields)
            changed_fields.pop("publish_time1", None)
            changed_fields.pop("publish_time2", None)
            changed_fields.pop("publish_round", None)
            if changed_fields:
                self._insert_event(
                    conn,
                    run_id,
                    now,
                    snapshot,
                    "CHANGED",
                    changed_fields,
                    old_tracked_json,
                    tracked_json,
                    content_hash,
                )
                counts["CHANGED"] += 1

        conn.execute(
            """
            UPDATE notice_current
            SET last_seen_at = ?, last_crawled_at = ?, publish_date = ?, detail_url = ?,
                content_hash = ?, status = 'ACTIVE', tracked_json = ?, raw_json = ?,
                asset_fingerprint = ?, absent_complete_runs = 0, last_detail_status = 'EXISTS',
                first_absent_at = ''
            WHERE notice_kind = ? AND notice_id = ?
            """,
            (
                now,
                now,
                snapshot.publish_date,
                snapshot.detail_url,
                content_hash,
                tracked_json,
                raw_json,
                asset_fingerprint,
                snapshot.notice_kind,
                snapshot.notice_id,
            ),
        )

    def _find_matching_repost(
        self,
        conn: sqlite3.Connection,
        snapshot: HistorySnapshot,
        asset_fingerprint: str,
    ) -> sqlite3.Row | None:
        if not asset_fingerprint:
            return None
        rows = conn.execute(
            """
            SELECT * FROM notice_current
            WHERE notice_kind = ?
              AND notice_id <> ?
              AND asset_fingerprint = ?
            ORDER BY last_seen_at DESC, first_seen_at DESC
            LIMIT 1
            """,
            (snapshot.notice_kind, snapshot.notice_id, asset_fingerprint),
        ).fetchall()
        for row in rows:
            if str(row["publish_date"] or "") != snapshot.publish_date:
                return row
        rows = conn.execute(
            """
            SELECT * FROM notice_current
            WHERE notice_kind = ?
              AND notice_id <> ?
            ORDER BY last_seen_at DESC, first_seen_at DESC
            """,
            (snapshot.notice_kind, snapshot.notice_id),
        ).fetchall()
        for row in rows:
            if str(row["publish_date"] or "") == snapshot.publish_date:
                continue
            tracked = _first_json_object(str(row["tracked_json"] or ""))
            if _is_legacy_repost_match(tracked, snapshot.tracked_fields):
                return row
        return None

    def _find_matching_same_asset_name(
        self,
        conn: sqlite3.Connection,
        snapshot: HistorySnapshot,
        asset_fingerprint: str,
    ) -> sqlite3.Row | None:
        asset_name_key = _asset_name_key(snapshot.tracked_fields)
        if not asset_name_key:
            return None
        rows = conn.execute(
            """
            SELECT * FROM notice_current
            WHERE notice_kind = ?
              AND notice_id <> ?
            ORDER BY last_seen_at DESC, first_seen_at DESC
            """,
            (snapshot.notice_kind, snapshot.notice_id),
        ).fetchall()
        for row in rows:
            if asset_fingerprint and str(row["asset_fingerprint"] or "") == asset_fingerprint:
                continue
            tracked = _first_json_object(str(row["tracked_json"] or ""))
            if _is_legacy_repost_match(tracked, snapshot.tracked_fields):
                continue
            if _asset_name_key(tracked) == asset_name_key:
                return row
        return None

    def _record_missing(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        now: str,
        notice_kind: str,
        start_date: str,
        end_date: str,
        seen_keys: set[str],
        seen_asset_fingerprints: set[str],
        counts: dict[str, int],
        missing_exists_validator: MissingExistsValidator | None,
    ) -> None:
        rows = conn.execute(
            """
            SELECT * FROM notice_current
            WHERE notice_kind = ? AND status <> 'REMOVED'
            """,
            (notice_kind,),
        ).fetchall()
        for row in rows:
            notice_id = str(row["notice_id"])
            if notice_id in seen_keys:
                continue
            asset_fingerprint = str(row["asset_fingerprint"] or "")
            if asset_fingerprint and asset_fingerprint in seen_asset_fingerprints:
                continue
            publish_date = str(row["publish_date"])
            if not _date_in_range(publish_date, start_date, end_date):
                continue
            snapshot = HistorySnapshot(
                notice_kind=notice_kind,
                notice_id=notice_id,
                publish_date=publish_date,
                detail_url=str(row["detail_url"]),
                tracked_fields=json.loads(str(row["tracked_json"])),
                raw_payload=json.loads(str(row["raw_json"])),
            )
            probe_status = "NOT_FOUND"
            if missing_exists_validator is not None:
                try:
                    probe_status = _probe_status(missing_exists_validator(snapshot))
                except Exception:
                    probe_status = "ACCESS_ERROR"

            old_tracked_json = str(row["tracked_json"])
            raw_json = str(row["raw_json"])
            self._insert_observation(
                conn,
                run_id,
                now,
                snapshot,
                search_present=False,
                detail_status=probe_status,
                asset_fingerprint=str(row["asset_fingerprint"] or ""),
                content_hash=str(row["content_hash"]),
                tracked_json=old_tracked_json,
                raw_json=raw_json,
            )
            old_status = str(row["status"])
            if probe_status == "ACCESS_ERROR":
                self._insert_event(
                    conn, run_id, now, snapshot, "CHECK_FAILED", {}, old_tracked_json, "", str(row["content_hash"])
                )
                counts["CHECK_FAILED"] += 1
                conn.execute(
                    """
                    UPDATE notice_current SET last_crawled_at = ?, last_detail_status = ?
                    WHERE notice_kind = ? AND notice_id = ?
                    """,
                    (now, probe_status, notice_kind, notice_id),
                )
                continue

            if probe_status == "EXISTS":
                if old_status != "DELISTED":
                    self._insert_event(
                        conn, run_id, now, snapshot, "DELISTED", {}, old_tracked_json, "", str(row["content_hash"])
                    )
                    counts["DELISTED"] += 1
                conn.execute(
                    """
                    UPDATE notice_current
                    SET status = 'DELISTED', absent_complete_runs = 0, first_absent_at = ?,
                        last_detail_status = ?, last_crawled_at = ?
                    WHERE notice_kind = ? AND notice_id = ?
                    """,
                    (now, probe_status, now, notice_kind, notice_id),
                )
                continue

            absent_runs = int(row["absent_complete_runs"] or 0) + 1
            event_type = "REMOVAL_PENDING" if absent_runs == 1 else "REMOVED"
            if event_type == "REMOVED" and old_status == "REMOVED":
                continue
            self._insert_event(
                conn, run_id, now, snapshot, event_type, {}, old_tracked_json, "", str(row["content_hash"])
            )
            counts[event_type] += 1
            conn.execute(
                """
                UPDATE notice_current
                SET status = ?, absent_complete_runs = ?, first_absent_at = COALESCE(NULLIF(first_absent_at, ''), ?),
                    last_detail_status = ?, last_crawled_at = ?
                WHERE notice_kind = ? AND notice_id = ?
                """,
                (event_type, absent_runs, now, probe_status, now, notice_kind, notice_id),
            )

    def _insert_observation(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        now: str,
        snapshot: HistorySnapshot,
        search_present: bool,
        detail_status: str,
        asset_fingerprint: str,
        content_hash: str,
        tracked_json: str,
        raw_json: str,
    ) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO notice_snapshots (
                run_id, notice_kind, notice_id, observed_at, search_present, detail_status,
                publish_time1_ms, publish_time2_ms, publish_round, asset_identity_hash,
                content_hash, tracked_json, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot.notice_kind,
                snapshot.notice_id,
                now,
                int(search_present),
                detail_status,
                snapshot.publish_time1,
                snapshot.publish_time2,
                2 if snapshot.publish_time2 else (1 if snapshot.publish_time1 else None),
                asset_fingerprint,
                content_hash,
                tracked_json,
                raw_json,
            ),
        )

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        now: str,
        snapshot: HistorySnapshot,
        event_type: str,
        changed_fields: dict[str, Any],
        old_tracked_json: str,
        new_tracked_json: str,
        content_hash: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO notice_history (
                run_id, notice_kind, notice_id, event_type, changed_fields_json,
                old_tracked_json, new_tracked_json, content_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                snapshot.notice_kind,
                snapshot.notice_id,
                event_type,
                _json_dump(changed_fields),
                old_tracked_json,
                new_tracked_json,
                content_hash,
                now,
            ),
        )

    def _finish_run(self, conn: sqlite3.Connection, run_id: int, counts: dict[str, int], run_status: str) -> None:
        conn.execute(
            """
            UPDATE crawl_runs
            SET new_count = ?, changed_count = ?, missing_count = ?,
                reappeared_count = ?, suspect_repost_count = ?,
                same_asset_name_count = ?, completed_at = ?, run_status = ?
            WHERE id = ?
            """,
            (
                counts["NEW"],
                counts["CHANGED"],
                counts["MISSING"],
                counts["REAPPEARED"],
                counts["SUSPECT_REPOST"],
                counts["SAME_ASSET_NAME"],
                _now_text(),
                run_status,
                run_id,
            ),
        )


def _empty_counts() -> dict[str, int]:
    return EventCounts({event_type: 0 for event_type in EVENT_TYPES[:6]})


def _probe_status(value: bool | str) -> str:
    if value is True:
        return "EXISTS"
    if value is False:
        return "NOT_FOUND"
    text = str(value or "").upper()
    return text if text in {"EXISTS", "NOT_FOUND", "ACCESS_ERROR"} else "ACCESS_ERROR"


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()}
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _backfill_asset_fingerprints(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT notice_kind, notice_id, tracked_json
        FROM notice_current
        WHERE asset_fingerprint = ''
        """
    ).fetchall()
    for row in rows:
        tracked = _first_json_object(str(row["tracked_json"] or ""))
        fingerprint = _asset_fingerprint(tracked)
        if not fingerprint:
            continue
        conn.execute(
            """
            UPDATE notice_current
            SET asset_fingerprint = ?
            WHERE notice_kind = ? AND notice_id = ?
            """,
            (fingerprint, row["notice_kind"], row["notice_id"]),
        )


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(_json_dump(value).encode("utf-8")).hexdigest()


def _comparable_tracked_fields(fields: dict[str, Any]) -> dict[str, Any]:
    comparable = dict(fields)
    comparable.pop("detail_url", None)
    comparable.pop("group", None)
    if "properties" in comparable:
        comparable["properties"] = _normalized_properties(comparable["properties"])
    if "asset_name" in comparable:
        comparable["asset_name"] = _normalized_asset_value(comparable["asset_name"])
    return comparable


def _cross_id_comparable_tracked_fields(fields: dict[str, Any]) -> dict[str, Any]:
    comparable = _comparable_tracked_fields(fields)
    comparable.pop("publish_date", None)
    comparable.pop("detail_url", None)
    comparable.pop("notice_code", None)
    comparable.pop("property_place", None)
    return comparable


def _asset_fingerprint(fields: dict[str, Any]) -> str:
    asset_value: Any = fields.get("properties") or fields.get("asset_name") or ""
    normalized = {
        "asset": _normalized_properties(asset_value) if isinstance(asset_value, list) else _normalized_asset_value(asset_value),
        "owner_name": _normalize_text(fields.get("owner_name", "")),
        "province": _normalize_text(fields.get("province", "")),
        "property_place": _normalize_text(fields.get("property_place", "")),
    }
    if not normalized["asset"]:
        return ""
    return hashlib.sha256(_json_dump(normalized).encode("utf-8")).hexdigest()


def _asset_name_key(fields: dict[str, Any]) -> str:
    asset_value: Any = fields.get("properties") or fields.get("asset_name") or ""
    normalized = _normalized_properties(asset_value) if isinstance(asset_value, list) else _normalized_asset_value(asset_value)
    return _json_dump(normalized) if normalized else ""


def _is_legacy_repost_match(old: dict[str, Any], new: dict[str, Any]) -> bool:
    if _asset_name_key(old) != _asset_name_key(new):
        return False
    if not _is_specific_asset_name(old):
        return False
    old_place = _normalize_text(old.get("property_place", ""))
    new_place = _normalize_text(new.get("property_place", ""))
    if old_place and new_place and old_place != new_place:
        return False
    for field_name in ("owner_name", "province"):
        if _normalize_text(old.get(field_name, "")) != _normalize_text(new.get(field_name, "")):
            return False
    for field_name in ("start_price", "deposit"):
        if _normalize_number_text(old.get(field_name, "")) != _normalize_number_text(new.get(field_name, "")):
            return False
    return True


def _is_specific_asset_name(fields: dict[str, Any]) -> bool:
    asset_value: Any = fields.get("asset_name") or ""
    text = _normalize_text(asset_value)
    return len(text) >= 100


def _normalized_properties(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    properties = []
    for item in value:
        if not isinstance(item, dict):
            continue
        properties.append(
            {
                "asset_name": _normalize_text(item.get("asset_name", "")),
                "amount": _normalize_number_text(item.get("amount", "")),
                "quality": _normalize_text(item.get("quality", "")),
                "start_price": _normalize_number_text(item.get("start_price", "")),
            }
        )
    return sorted(properties, key=_json_dump)


def _normalized_asset_value(value: Any) -> list[str]:
    text = _normalize_text(value)
    text = re.sub(r"^thông báo việc đấu giá đối với danh mục tài sản:\s*", "", text)
    if not text:
        return []
    parts = [_normalize_text(part) for part in text.split(",")]
    return sorted(part for part in parts if part)


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.;")


def _normalize_number_text(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _diff_fields(old: dict[str, Any], new: dict[str, Any]) -> dict[str, dict[str, Any]]:
    changed: dict[str, dict[str, Any]] = {}
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            changed[key] = {"old": old.get(key), "new": new.get(key)}
    return changed


def _remove_changed_ignored_fields(changed_fields: dict[str, dict[str, Any]]) -> None:
    for field_name in ("detail_url", "group", "province"):
        changed_fields.pop(field_name, None)


def _repost_change_fields(matched: sqlite3.Row, snapshot: HistorySnapshot) -> dict[str, dict[str, Any]]:
    matched_info = {
        "notice_id": str(matched["notice_id"] or ""),
        "publish_date": str(matched["publish_date"] or ""),
        "detail_url": str(matched["detail_url"] or ""),
    }
    snapshot_info = {
        "notice_id": snapshot.notice_id,
        "publish_date": snapshot.publish_date,
        "detail_url": snapshot.detail_url,
    }
    old_info, new_info = _order_repost_pair(matched_info, snapshot_info)
    return {
        "notice_id": {"old": old_info["notice_id"], "new": new_info["notice_id"]},
        "publish_date": {"old": old_info["publish_date"], "new": new_info["publish_date"]},
        "detail_url": {"old": old_info["detail_url"], "new": new_info["detail_url"]},
        "match_type": {"old": "", "new": "exact_asset_fingerprint"},
    }


def _same_asset_name_change_fields(matched: sqlite3.Row, snapshot: HistorySnapshot) -> dict[str, dict[str, Any]]:
    old_tracked = _first_json_object(str(matched["tracked_json"] or ""))
    changes = _repost_change_fields(matched, snapshot)
    for field_name in ("asset_name", "property_place", "owner_name", "province", "start_price", "deposit"):
        old_value = old_tracked.get(field_name)
        new_value = snapshot.tracked_fields.get(field_name)
        if old_value != new_value or field_name == "asset_name":
            changes[field_name] = {"old": old_value, "new": new_value}
    changes["match_type"] = {"old": "", "new": "same_asset_name"}
    return changes


def _order_repost_pair(first: dict[str, str], second: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    first_date = _date_sort_key(first.get("publish_date", ""))
    second_date = _date_sort_key(second.get("publish_date", ""))
    if first_date != second_date:
        return (first, second) if first_date < second_date else (second, first)
    first_id = _number_sort_key(first.get("notice_id", ""))
    second_id = _number_sort_key(second.get("notice_id", ""))
    return (first, second) if first_id <= second_id else (second, first)


def _date_sort_key(value: str) -> tuple[int, int, int]:
    try:
        parsed = datetime.strptime(value, "%d/%m/%Y")
    except ValueError:
        return (9999, 12, 31)
    return (parsed.year, parsed.month, parsed.day)


def _number_sort_key(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _history_event_row(row: sqlite3.Row) -> HistoryEventRow:
    changed_fields = json.loads(str(row["changed_fields_json"] or "{}"))
    changed_fields = _normalize_history_change_fields(changed_fields)
    notice_id = str(row["notice_id"] or "")
    tracked = _first_json_object(str(row["new_tracked_json"] or "")) or _first_json_object(
        str(row["old_tracked_json"] or "")
    )
    publish_date = str(row["publish_date"] or tracked.get("publish_date") or "")
    detail_url = str(row["detail_url"] or tracked.get("detail_url") or "")
    return HistoryEventRow(
        created_at=str(row["created_at"] or ""),
        notice_kind=str(row["notice_kind"] or ""),
        event_type=str(row["event_type"] or ""),
        notice_id=notice_id,
        publish_date=publish_date,
        detail_url=detail_url,
        changed_fields=", ".join(sorted(changed_fields)) if changed_fields else "",
        changed_details=_format_change_details(changed_fields),
        old_values=_format_change_side(changed_fields, "old"),
        new_values=_format_change_side(changed_fields, "new"),
        matched_notice_id=_matched_notice_id(changed_fields, notice_id),
    )


def _normalize_history_change_fields(changed_fields: dict[str, Any]) -> dict[str, Any]:
    if "notice_id" in changed_fields or "matched_notice_id" not in changed_fields:
        return changed_fields
    old_info = {
        "notice_id": _change_old_value(changed_fields, "matched_notice_id"),
        "publish_date": _change_old_value(changed_fields, "matched_publish_date"),
        "detail_url": _change_old_value(changed_fields, "matched_detail_url"),
    }
    new_info = {
        "notice_id": _change_new_value(changed_fields, "matched_notice_id"),
        "publish_date": _change_new_value(changed_fields, "matched_publish_date"),
        "detail_url": _change_new_value(changed_fields, "matched_detail_url"),
    }
    old_info, new_info = _order_repost_pair(old_info, new_info)
    return {
        "notice_id": {"old": old_info["notice_id"], "new": new_info["notice_id"]},
        "publish_date": {"old": old_info["publish_date"], "new": new_info["publish_date"]},
        "detail_url": {"old": old_info["detail_url"], "new": new_info["detail_url"]},
        "match_type": {
            "old": _change_old_value(changed_fields, "match_type"),
            "new": _change_new_value(changed_fields, "match_type"),
        },
    }


def _should_show_history_row(row: HistoryEventRow) -> bool:
    if row.event_type != "SUSPECT_REPOST":
        return True
    old_date, new_date = _repost_dates_from_details(row.changed_details)
    return not old_date or not new_date or old_date != new_date


def _repost_dates_from_details(details: str) -> tuple[str, str]:
    values = {}
    for line in details.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values.get("Ngày đăng cũ", ""), values.get("Ngày đăng mới", "")


def _format_change_details(changed_fields: dict[str, Any]) -> str:
    if not changed_fields:
        return ""
    matched_notice_id = _matched_notice_id(changed_fields)
    if matched_notice_id:
        return (
            f"Tin cũ: {_change_old_value(changed_fields, 'notice_id')}\n"
            f"Tin mới: {_change_new_value(changed_fields, 'notice_id')}\n"
            f"Kiểu khớp: {_change_new_value(changed_fields, 'match_type')}\n"
            f"Ngày đăng cũ: {_change_old_value(changed_fields, 'publish_date')}\n"
            f"Ngày đăng mới: {_change_new_value(changed_fields, 'publish_date')}"
        )
    details = []
    for field_name in sorted(changed_fields):
        change = changed_fields[field_name]
        if not isinstance(change, dict):
            continue
        details.append(
            f"{field_name}:\n"
            f"Cũ: {_format_change_value(change.get('old'))}\n"
            f"Mới: {_format_change_value(change.get('new'))}"
        )
    return "\n\n".join(details)


def _matched_notice_id(changed_fields: dict[str, Any], current_notice_id: str = "") -> str:
    old_id = _change_old_value(changed_fields, "notice_id")
    new_id = _change_new_value(changed_fields, "notice_id")
    if current_notice_id and old_id == current_notice_id:
        return new_id
    if current_notice_id and new_id == current_notice_id:
        return old_id
    return old_id or new_id


def _change_old_value(changed_fields: dict[str, Any], field_name: str) -> str:
    change = changed_fields.get(field_name)
    if isinstance(change, dict):
        return _format_change_value(change.get("old"))
    return ""


def _change_new_value(changed_fields: dict[str, Any], field_name: str) -> str:
    change = changed_fields.get(field_name)
    if isinstance(change, dict):
        return _format_change_value(change.get("new"))
    return ""


def _format_change_side(changed_fields: dict[str, Any], side: str) -> str:
    if not changed_fields:
        return ""
    values = []
    for field_name in sorted(changed_fields):
        change = changed_fields[field_name]
        if isinstance(change, dict):
            values.append(f"{field_name}: {_format_change_value(change.get(side))}")
    return "\n".join(values)


def _format_change_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _first_json_object(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _date_in_range(value: str, start_date: str, end_date: str) -> bool:
    if not value:
        return not start_date and not end_date
    parsed = datetime.strptime(value, "%d/%m/%Y")
    if start_date and parsed < datetime.strptime(start_date, "%d/%m/%Y"):
        return False
    if end_date and parsed > datetime.strptime(end_date, "%d/%m/%Y"):
        return False
    return True


def _now_text(value: datetime | None = None) -> str:
    return (value or datetime.now()).isoformat(timespec="seconds")

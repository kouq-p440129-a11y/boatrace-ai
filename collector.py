import argparse
import json
import math
import os
import sqlite3
from datetime import UTC, datetime
from typing import Iterable, List, Optional

import main


DEFAULT_PROGRESS_NAME = "default"
DEFAULT_DB_PATH = os.path.join("data", "collector_validation.sqlite3")


def utc_now_iso():
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def connect_db(db_path: str) -> sqlite3.Connection:
    ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS collection_targets (
    race_id TEXT PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    discovered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS races (
    race_id TEXT PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    place_code TEXT,
    race_date TEXT,
    race_no INTEGER,
    title TEXT,
    collected_at TEXT NOT NULL,
    race_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS race_entries (
    race_id TEXT NOT NULL,
    lane INTEGER NOT NULL,
    racer_name TEXT,
    racer_class TEXT,
    avg_st TEXT,
    national_win_rate TEXT,
    national_place2_rate TEXT,
    local_win_rate TEXT,
    local_place2_rate TEXT,
    motor_place2_rate TEXT,
    boat_place2_rate TEXT,
    foul_count INTEGER,
    entry_json TEXT NOT NULL,
    PRIMARY KEY (race_id, lane),
    FOREIGN KEY (race_id) REFERENCES races(race_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS exhibitions (
    race_id TEXT NOT NULL,
    lane INTEGER NOT NULL,
    exhibition_time TEXT,
    exhibition_rank TEXT,
    exhibition_diff TEXT,
    exhibition_st TEXT,
    exhibition_st_rank TEXT,
    entry_stability TEXT,
    exhibition_json TEXT NOT NULL,
    PRIMARY KEY (race_id, lane),
    FOREIGN KEY (race_id) REFERENCES races(race_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS weather (
    race_id TEXT PRIMARY KEY,
    weather TEXT,
    wind_speed TEXT,
    wind_direction TEXT,
    wave_height TEXT,
    water_temperature TEXT,
    weather_json TEXT NOT NULL,
    FOREIGN KEY (race_id) REFERENCES races(race_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS results (
    race_id TEXT PRIMARY KEY,
    trifecta TEXT,
    payout TEXT,
    popularity TEXT,
    kimarite TEXT,
    wind_speed TEXT,
    wind_direction TEXT,
    wave_height TEXT,
    water_temperature TEXT,
    result_url TEXT,
    collected_at TEXT NOT NULL,
    result_json TEXT NOT NULL,
    FOREIGN KEY (race_id) REFERENCES races(race_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS collector_progress (
    progress_name TEXT PRIMARY KEY,
    target_count INTEGER NOT NULL DEFAULT 0,
    discovered_count INTEGER NOT NULL DEFAULT 0,
    processed_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    pending_retry_count INTEGER NOT NULL DEFAULT 0,
    last_race_id TEXT,
    started_at TEXT,
    updated_at TEXT,
    last_summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS retry_queue (
    race_id TEXT PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    queued_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS collection_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_id TEXT NOT NULL,
    url TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL
);
"""


def initialize_database(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def build_race_id(url: str) -> str:
    place_code, race_date, race_no = main.get_race_parts(url)
    normalized_race_no = main.race_no_to_int(race_no)
    return f"{place_code}_{race_date}_{normalized_race_no:02d}"


def normalize_places(place_codes: Optional[str]) -> List[str]:
    if not place_codes:
        return list(main.JCD_MAP.keys())
    return main.parse_place_codes_input(place_codes)


def discover_race_urls(target_count: int, place_codes: Optional[Iterable[str]] = None, max_days: int = 120, base_date: str = "") -> List[str]:
    codes = list(place_codes or main.JCD_MAP.keys())
    if target_count <= 0 or not codes:
        return []
    per_place_limit = max(1, math.ceil(target_count / len(codes)))
    urls: List[str] = []
    seen = set()
    for code in codes:
        recent = main.get_recent_completed_race_urls(code, base_date, limit=per_place_limit, max_days=max_days, debug=False)
        for url in recent:
            if url not in seen:
                seen.add(url)
                urls.append(url)
            if len(urls) >= target_count:
                return urls[:target_count]
    return urls[:target_count]


class RaceCollector:
    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        parse_race_detail_fn=None,
        extract_result_info_fn=None,
    ) -> None:
        self.db_path = db_path
        self.parse_race_detail_fn = parse_race_detail_fn or main.parse_race_detail
        self.extract_result_info_fn = extract_result_info_fn or main.extract_result_info
        self.conn = connect_db(db_path)
        initialize_database(self.conn)

    def close(self) -> None:
        self.conn.close()

    def collect(self, urls: Iterable[str], progress_name: str = DEFAULT_PROGRESS_NAME) -> dict:
        urls = list(urls)
        self.seed_targets(urls)
        summary = {
            "target_count": len(urls),
            "processed_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "retry_count": 0,
            "duplicate_count": 0,
            "pending_retry_count": self.pending_retry_count(),
        }
        self.ensure_progress_row(progress_name, len(urls))
        for url in urls:
            race_id = build_race_id(url)
            summary["processed_count"] += 1
            if self.race_exists(race_id):
                self.record_attempt(race_id, url, "collect", "duplicate")
                summary["duplicate_count"] += 1
                self.resolve_retry_if_present(race_id)
                self.save_progress(progress_name, summary, race_id)
                continue
            try:
                race_data, result = self.fetch_bundle(url)
                self.save_bundle(race_id, url, race_data, result)
                self.record_attempt(race_id, url, "collect", "success")
                summary["success_count"] += 1
                self.resolve_retry_if_present(race_id)
            except Exception as exc:
                self.record_attempt(race_id, url, "collect", "failure", str(exc))
                self.enqueue_retry(race_id, url, str(exc))
                summary["failure_count"] += 1
            summary["pending_retry_count"] = self.pending_retry_count()
            self.save_progress(progress_name, summary, race_id)
        return self.refresh_summary(progress_name, summary)

    def process_retry_queue(self, progress_name: str = DEFAULT_PROGRESS_NAME, limit: Optional[int] = None) -> dict:
        queue_rows = self.conn.execute(
            "SELECT race_id, url FROM retry_queue WHERE status = 'pending' ORDER BY updated_at, race_id"
            + (" LIMIT ?" if limit else ""),
            ((limit,) if limit else ()),
        ).fetchall()
        summary = {
            "target_count": self.count_table("collection_targets"),
            "processed_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "retry_count": 0,
            "duplicate_count": 0,
            "pending_retry_count": self.pending_retry_count(),
        }
        self.ensure_progress_row(progress_name, summary["target_count"])
        for row in queue_rows:
            race_id = row["race_id"]
            url = row["url"]
            summary["processed_count"] += 1
            summary["retry_count"] += 1
            if self.race_exists(race_id):
                self.record_attempt(race_id, url, "retry", "duplicate")
                self.resolve_retry(race_id)
                summary["duplicate_count"] += 1
                self.save_progress(progress_name, summary, race_id)
                continue
            try:
                race_data, result = self.fetch_bundle(url)
                self.save_bundle(race_id, url, race_data, result)
                self.record_attempt(race_id, url, "retry", "success")
                self.resolve_retry(race_id)
                summary["success_count"] += 1
            except Exception as exc:
                self.record_attempt(race_id, url, "retry", "failure", str(exc))
                self.enqueue_retry(race_id, url, str(exc), increment_existing=True)
                summary["failure_count"] += 1
            summary["pending_retry_count"] = self.pending_retry_count()
            self.save_progress(progress_name, summary, race_id)
        return self.refresh_summary(progress_name, summary)

    def fetch_bundle(self, url: str):
        race_data = self.parse_race_detail_fn(url, include_deep=False, debug=False)
        result = self.extract_result_info_fn(url, debug=False)
        if not result.get("3連単"):
            raise ValueError("結果3連単が取得できませんでした")
        if len(race_data.get("出走表", [])) != 6:
            raise ValueError(f"6艇揃っていません: {len(race_data.get('出走表', []))}艇")
        return race_data, result

    def save_bundle(self, race_id: str, url: str, race_data: dict, result: dict) -> None:
        now = utc_now_iso()
        weather = merged_weather(race_data.get("水面気象情報", {}) or {}, result or {})
        entries = race_data.get("出走表", []) or []
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO races (race_id, url, place_code, race_date, race_no, title, collected_at, race_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(race_id) DO UPDATE SET
                    url = excluded.url,
                    place_code = excluded.place_code,
                    race_date = excluded.race_date,
                    race_no = excluded.race_no,
                    title = excluded.title,
                    collected_at = excluded.collected_at,
                    race_json = excluded.race_json
                """,
                (
                    race_id,
                    url,
                    race_data.get("場コード", ""),
                    race_data.get("日付", ""),
                    race_data.get("レース番号", 0),
                    race_data.get("レース", main.get_race_title(url)),
                    now,
                    json.dumps(race_data, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            self.conn.execute("DELETE FROM race_entries WHERE race_id = ?", (race_id,))
            self.conn.execute("DELETE FROM exhibitions WHERE race_id = ?", (race_id,))
            for entry in entries:
                lane = int(entry.get("枠", 0) or 0)
                entry_json = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
                self.conn.execute(
                    """
                    INSERT INTO race_entries (
                        race_id, lane, racer_name, racer_class, avg_st, national_win_rate,
                        national_place2_rate, local_win_rate, local_place2_rate,
                        motor_place2_rate, boat_place2_rate, foul_count, entry_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        race_id,
                        lane,
                        entry.get("選手名", ""),
                        entry.get("級別", ""),
                        entry.get("平均ST", ""),
                        entry.get("全国勝率", ""),
                        entry.get("全国2連率", ""),
                        entry.get("当地勝率", ""),
                        entry.get("当地2連率", ""),
                        entry.get("モーター2連率", ""),
                        entry.get("ボート2連率", ""),
                        int(entry.get("F持ち", 0) or 0),
                        entry_json,
                    ),
                )
                self.conn.execute(
                    """
                    INSERT INTO exhibitions (
                        race_id, lane, exhibition_time, exhibition_rank, exhibition_diff,
                        exhibition_st, exhibition_st_rank, entry_stability, exhibition_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        race_id,
                        lane,
                        entry.get("展示タイム", ""),
                        entry.get("展示順位", ""),
                        entry.get("展示差", ""),
                        entry.get("展示ST", ""),
                        entry.get("展示ST順位", ""),
                        entry.get("進入安定度", ""),
                        entry_json,
                    ),
                )
            self.conn.execute(
                """
                INSERT INTO weather (race_id, weather, wind_speed, wind_direction, wave_height, water_temperature, weather_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(race_id) DO UPDATE SET
                    weather = excluded.weather,
                    wind_speed = excluded.wind_speed,
                    wind_direction = excluded.wind_direction,
                    wave_height = excluded.wave_height,
                    water_temperature = excluded.water_temperature,
                    weather_json = excluded.weather_json
                """,
                (
                    race_id,
                    weather.get("天候", ""),
                    weather.get("風速", ""),
                    weather.get("風向", ""),
                    weather.get("波高", ""),
                    weather.get("水温", ""),
                    json.dumps(weather, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            self.conn.execute(
                """
                INSERT INTO results (
                    race_id, trifecta, payout, popularity, kimarite, wind_speed, wind_direction,
                    wave_height, water_temperature, result_url, collected_at, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(race_id) DO UPDATE SET
                    trifecta = excluded.trifecta,
                    payout = excluded.payout,
                    popularity = excluded.popularity,
                    kimarite = excluded.kimarite,
                    wind_speed = excluded.wind_speed,
                    wind_direction = excluded.wind_direction,
                    wave_height = excluded.wave_height,
                    water_temperature = excluded.water_temperature,
                    result_url = excluded.result_url,
                    collected_at = excluded.collected_at,
                    result_json = excluded.result_json
                """,
                (
                    race_id,
                    result.get("3連単", ""),
                    result.get("3連単払戻", ""),
                    result.get("人気", ""),
                    result.get("決まり手", ""),
                    result.get("風速", weather.get("風速", "")),
                    result.get("風向", weather.get("風向", "")),
                    result.get("波高", weather.get("波高", "")),
                    result.get("水温", weather.get("水温", "")),
                    result.get("結果URL", ""),
                    now,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def seed_targets(self, urls: Iterable[str]) -> None:
        now = utc_now_iso()
        with self.conn:
            for url in urls:
                race_id = build_race_id(url)
                self.conn.execute(
                    "INSERT OR IGNORE INTO collection_targets (race_id, url, discovered_at) VALUES (?, ?, ?)",
                    (race_id, url, now),
                )

    def seed_retry_from_success(self) -> Optional[str]:
        row = self.conn.execute("SELECT race_id, url FROM races ORDER BY race_id LIMIT 1").fetchone()
        if not row:
            return None
        self.enqueue_retry(row["race_id"], row["url"], "manual retry verification", increment_existing=False)
        return row["race_id"]

    def enqueue_retry(self, race_id: str, url: str, reason: str, increment_existing: bool = True) -> None:
        now = utc_now_iso()
        row = self.conn.execute("SELECT attempts, status FROM retry_queue WHERE race_id = ?", (race_id,)).fetchone()
        attempts = 1
        if row:
            attempts = int(row["attempts"] or 0) + (1 if increment_existing else 0)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO retry_queue (race_id, url, reason, status, attempts, last_error, queued_at, updated_at, resolved_at)
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, NULL)
                ON CONFLICT(race_id) DO UPDATE SET
                    url = excluded.url,
                    reason = excluded.reason,
                    status = 'pending',
                    attempts = ?,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at,
                    resolved_at = NULL
                """,
                (race_id, url, reason, attempts, reason, now, now, attempts),
            )

    def resolve_retry_if_present(self, race_id: str) -> None:
        row = self.conn.execute("SELECT race_id FROM retry_queue WHERE race_id = ? AND status = 'pending'", (race_id,)).fetchone()
        if row:
            self.resolve_retry(race_id)

    def resolve_retry(self, race_id: str) -> None:
        now = utc_now_iso()
        with self.conn:
            self.conn.execute(
                "UPDATE retry_queue SET status = 'resolved', updated_at = ?, resolved_at = ? WHERE race_id = ?",
                (now, now, race_id),
            )

    def record_attempt(self, race_id: str, url: str, phase: str, status: str, error_message: str = "") -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO collection_attempts (race_id, url, phase, status, error_message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (race_id, url, phase, status, error_message, utc_now_iso()),
            )

    def race_exists(self, race_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM races WHERE race_id = ?", (race_id,)).fetchone()
        return bool(row)

    def pending_retry_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM retry_queue WHERE status = 'pending'").fetchone()[0]

    def count_table(self, table_name: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

    def ensure_progress_row(self, progress_name: str, target_count: int) -> None:
        now = utc_now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO collector_progress (progress_name, target_count, discovered_count, started_at, updated_at, last_summary_json)
                VALUES (?, ?, ?, ?, ?, '{}')
                ON CONFLICT(progress_name) DO UPDATE SET
                    target_count = CASE WHEN excluded.target_count > collector_progress.target_count THEN excluded.target_count ELSE collector_progress.target_count END,
                    discovered_count = CASE WHEN excluded.discovered_count > collector_progress.discovered_count THEN excluded.discovered_count ELSE collector_progress.discovered_count END,
                    updated_at = excluded.updated_at,
                    started_at = COALESCE(collector_progress.started_at, excluded.started_at)
                """,
                (progress_name, target_count, target_count, now, now),
            )

    def save_progress(self, progress_name: str, summary: dict, last_race_id: Optional[str]) -> None:
        refreshed = self.refresh_summary(progress_name, summary)
        with self.conn:
            self.conn.execute(
                """
                UPDATE collector_progress
                   SET target_count = ?,
                       discovered_count = ?,
                       processed_count = ?,
                       success_count = ?,
                       failure_count = ?,
                       retry_count = ?,
                       duplicate_count = ?,
                       pending_retry_count = ?,
                       last_race_id = ?,
                       updated_at = ?,
                       last_summary_json = ?
                 WHERE progress_name = ?
                """,
                (
                    refreshed["target_count"],
                    refreshed["target_count"],
                    refreshed["processed_count"],
                    refreshed["success_count"],
                    refreshed["failure_count"],
                    refreshed["retry_count"],
                    refreshed["duplicate_count"],
                    refreshed["pending_retry_count"],
                    last_race_id,
                    utc_now_iso(),
                    json.dumps(refreshed, ensure_ascii=False, separators=(",", ":")),
                    progress_name,
                ),
            )

    def refresh_summary(self, progress_name: str, summary: dict) -> dict:
        summary = dict(summary)
        summary["target_count"] = max(summary.get("target_count", 0), self.count_table("collection_targets"))
        summary["success_count"] = self.count_table("races")
        summary["failure_count"] = self.conn.execute(
            "SELECT COUNT(*) FROM retry_queue WHERE status = 'pending'"
        ).fetchone()[0]
        summary["retry_count"] = self.conn.execute(
            "SELECT COUNT(*) FROM collection_attempts WHERE phase = 'retry'"
        ).fetchone()[0]
        summary["duplicate_count"] = self.conn.execute(
            "SELECT COUNT(*) FROM collection_attempts WHERE status = 'duplicate'"
        ).fetchone()[0]
        summary["pending_retry_count"] = self.pending_retry_count()
        progress = self.conn.execute(
            "SELECT processed_count FROM collector_progress WHERE progress_name = ?",
            (progress_name,),
        ).fetchone()
        if progress:
            summary["processed_count"] = max(summary.get("processed_count", 0), int(progress["processed_count"] or 0))
        return summary


def merged_weather(race_weather: dict, result: dict) -> dict:
    merged = dict(race_weather or {})
    for key in ["風速", "風向", "波高", "水温"]:
        if not merged.get(key) and result.get(key):
            merged[key] = result.get(key)
    if not merged.get("天候") and result.get("天候"):
        merged["天候"] = result.get("天候")
    return merged


def load_urls_from_file(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def run_collection(args) -> dict:
    collector = RaceCollector(db_path=args.db_path)
    try:
        if args.urls_file:
            urls = load_urls_from_file(args.urls_file)
        elif args.retry_only:
            urls = []
        else:
            place_codes = normalize_places(args.places)
            urls = discover_race_urls(args.target_count, place_codes=place_codes, max_days=args.max_days, base_date=args.base_date)
        collector.ensure_progress_row(args.progress_name, len(urls) or collector.count_table("collection_targets"))
        summary = collector.collect(urls, progress_name=args.progress_name) if urls else collector.refresh_summary(args.progress_name, {
            "target_count": collector.count_table("collection_targets"),
            "processed_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "retry_count": 0,
            "duplicate_count": 0,
            "pending_retry_count": collector.pending_retry_count(),
        })
        if args.seed_success_retry:
            collector.seed_retry_from_success()
        if args.process_retry_queue:
            summary = collector.process_retry_queue(progress_name=args.progress_name)
        collector.save_progress(args.progress_name, summary, None)
        return summary
    finally:
        collector.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect race data into SQLite with resume/retry support")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="SQLite database path")
    parser.add_argument("--progress-name", default=DEFAULT_PROGRESS_NAME, help="collector_progress row name")
    parser.add_argument("--target-count", type=int, default=1000, help="target number of races to collect")
    parser.add_argument("--places", default="", help="comma separated place names/codes")
    parser.add_argument("--max-days", type=int, default=120, help="max lookback days")
    parser.add_argument("--base-date", default="", help="base date YYYY-MM-DD")
    parser.add_argument("--urls-file", default="", help="collect explicit URLs from file")
    parser.add_argument("--process-retry-queue", action="store_true", help="process pending retry_queue rows after collection")
    parser.add_argument("--retry-only", action="store_true", help="skip discovery/collection and only operate on retry_queue")
    parser.add_argument("--seed-success-retry", action="store_true", help="enqueue one successful race into retry_queue for verification")
    return parser


def main_cli() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    summary = run_collection(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())

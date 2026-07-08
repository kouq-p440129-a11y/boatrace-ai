import argparse
import os
from typing import Dict, List

from collector import DEFAULT_DB_PATH, DEFAULT_PROGRESS_NAME, connect_db, initialize_database


def list_from_query(conn, query: str) -> List[str]:
    return [row[0] for row in conn.execute(query).fetchall()]


def collect_quality_metrics(db_path: str = DEFAULT_DB_PATH, progress_name: str = DEFAULT_PROGRESS_NAME) -> Dict[str, object]:
    conn = connect_db(db_path)
    try:
        initialize_database(conn)
        progress = conn.execute(
            "SELECT * FROM collector_progress WHERE progress_name = ?",
            (progress_name,),
        ).fetchone()
        target_count = progress["target_count"] if progress else conn.execute("SELECT COUNT(*) FROM collection_targets").fetchone()[0]
        success_count = conn.execute("SELECT COUNT(*) FROM races").fetchone()[0]
        failure_count = conn.execute("SELECT COUNT(*) FROM retry_queue WHERE status = 'pending'").fetchone()[0]
        retry_count = conn.execute("SELECT COUNT(*) FROM collection_attempts WHERE phase = 'retry'").fetchone()[0]
        duplicate_count = conn.execute("SELECT COUNT(*) FROM collection_attempts WHERE status = 'duplicate'").fetchone()[0]
        race_count = success_count
        race_entry_count = conn.execute("SELECT COUNT(*) FROM race_entries").fetchone()[0]
        exhibition_count = conn.execute("SELECT COUNT(*) FROM exhibitions").fetchone()[0]
        weather_count = conn.execute("SELECT COUNT(*) FROM weather").fetchone()[0]
        result_count = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]

        incomplete_races = list_from_query(
            conn,
            """
            SELECT r.race_id
              FROM races r
              LEFT JOIN race_entries e ON e.race_id = r.race_id
             GROUP BY r.race_id
            HAVING COUNT(e.lane) != 6
            ORDER BY r.race_id
            """,
        )
        missing_result_races = list_from_query(
            conn,
            """
            SELECT r.race_id
              FROM races r
              LEFT JOIN results x ON x.race_id = r.race_id
             WHERE x.race_id IS NULL OR COALESCE(x.trifecta, '') = ''
             ORDER BY r.race_id
            """,
        )
        weather_missing_races = list_from_query(
            conn,
            """
            SELECT r.race_id
              FROM races r
              LEFT JOIN weather w ON w.race_id = r.race_id
             WHERE w.race_id IS NULL
                OR (
                    COALESCE(NULLIF(w.weather, ''), NULLIF(w.wind_speed, ''), NULLIF(w.wind_direction, ''), NULLIF(w.wave_height, ''), NULLIF(w.water_temperature, '')) IS NULL
                )
             ORDER BY r.race_id
            """,
        )
        retry_queue_remaining = conn.execute(
            "SELECT COUNT(*) FROM retry_queue WHERE status = 'pending'"
        ).fetchone()[0]
        missing_data_count = len(incomplete_races) + len(missing_result_races) + len(weather_missing_races)

        return {
            "取得対象レース数": target_count,
            "取得成功件数": success_count,
            "取得失敗件数": failure_count,
            "retry件数": retry_count,
            "欠損データ件数": missing_data_count,
            "重複データ件数": duplicate_count,
            "race件数": race_count,
            "race_entry件数": race_entry_count,
            "exhibition件数": exhibition_count,
            "weather件数": weather_count,
            "result件数": result_count,
            "6艇揃っていないレース一覧": incomplete_races,
            "resultが存在しないレース一覧": missing_result_races,
            "weather欠損一覧": weather_missing_races,
            "retry_queue残件数": retry_queue_remaining,
        }
    finally:
        conn.close()


def render_markdown(metrics: Dict[str, object], title: str = "# Data Quality Report") -> str:
    lines = [title, "", "## Summary", ""]
    summary_keys = [
        "取得対象レース数",
        "取得成功件数",
        "取得失敗件数",
        "retry件数",
        "欠損データ件数",
        "重複データ件数",
        "race件数",
        "race_entry件数",
        "exhibition件数",
        "weather件数",
        "result件数",
        "retry_queue残件数",
    ]
    for key in summary_keys:
        lines.append(f"- {key}: {metrics.get(key, 0)}")

    lines.extend([
        "",
        "## Details",
        "",
        render_list_section("6艇揃っていないレース一覧", metrics.get("6艇揃っていないレース一覧", [])),
        "",
        render_list_section("resultが存在しないレース一覧", metrics.get("resultが存在しないレース一覧", [])),
        "",
        render_list_section("weather欠損一覧", metrics.get("weather欠損一覧", [])),
    ])
    return "\n".join(lines).rstrip() + "\n"


def render_list_section(title: str, values: List[str]) -> str:
    if not values:
        return f"### {title}\n\n- なし"
    body = "\n".join(f"- {value}" for value in values)
    return f"### {title}\n\n{body}"


def write_report(output_path: str, content: str) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(content)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate SQLite data quality report")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="SQLite database path")
    parser.add_argument("--progress-name", default=DEFAULT_PROGRESS_NAME, help="collector_progress row name")
    parser.add_argument("--output", default=os.path.join("reports", "data_quality_1000.md"), help="markdown output path")
    parser.add_argument("--strict", action="store_true", help="fail when quality issues remain")
    return parser


def main_cli() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    metrics = collect_quality_metrics(db_path=args.db_path, progress_name=args.progress_name)
    content = render_markdown(metrics)
    write_report(args.output, content)
    print(content)
    if args.strict:
        if (
            metrics["取得失敗件数"]
            or metrics["欠損データ件数"]
            or metrics["retry_queue残件数"]
        ):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())

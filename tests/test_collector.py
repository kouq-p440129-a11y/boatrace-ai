import os
import tempfile
import unittest

from collector import RaceCollector, build_race_id
from data_quality import collect_quality_metrics, render_markdown


SUCCESS_URL = "https://boaters-boatrace.com/race/mikuni/2026-07-01/1R/race-detail"
FAIL_URL = "https://boaters-boatrace.com/race/mikuni/2026-07-01/2R/race-detail"


def sample_race_data(url: str, entry_count: int = 6):
    entries = []
    for lane in range(1, entry_count + 1):
        entries.append(
            {
                "枠": lane,
                "選手名": f"選手{lane}",
                "級別": "A1",
                "平均ST": ".12",
                "全国勝率": "6.50",
                "全国2連率": "45.0",
                "当地勝率": "6.00",
                "当地2連率": "40.0",
                "モーター2連率": "35.0",
                "ボート2連率": "30.0",
                "F持ち": 0,
                "展示タイム": f"6.{50 + lane}",
                "展示順位": str(lane),
                "展示差": f"0.0{lane}",
                "展示ST": f"0.0{lane}",
                "展示ST順位": str(lane),
                "進入安定度": "高",
            }
        )
    return {
        "レースURL": url,
        "レース": "三国 2026-07-01 1R",
        "場コード": "mikuni",
        "日付": "2026-07-01",
        "レース番号": 1,
        "解析選手数": entry_count,
        "出走表": entries,
        "水面気象情報": {"天候": "晴", "風速": "3m", "風向": "北", "波高": "2cm", "水温": "24℃"},
    }


def sample_result(url: str):
    return {
        "3連単": "1-2-3",
        "3連単払戻": "1230円",
        "人気": "5",
        "決まり手": "逃げ",
        "風速": "3m",
        "風向": "北",
        "波高": "2cm",
        "水温": "24℃",
        "結果URL": url.replace("race-detail", "race-result"),
    }


class RaceCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "collector.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_collect_saves_sqlite_rows(self):
        collector = RaceCollector(
            db_path=self.db_path,
            parse_race_detail_fn=lambda url, include_deep=False, debug=False: sample_race_data(url),
            extract_result_info_fn=lambda url, debug=False: sample_result(url),
        )
        try:
            summary = collector.collect([SUCCESS_URL], progress_name="test")
            self.assertEqual(summary["success_count"], 1)
            self.assertEqual(collector.count_table("races"), 1)
            self.assertEqual(collector.count_table("race_entries"), 6)
            self.assertEqual(collector.count_table("exhibitions"), 6)
            self.assertEqual(collector.count_table("weather"), 1)
            self.assertEqual(collector.count_table("results"), 1)
        finally:
            collector.close()

    def test_resume_skips_existing_races_without_duplication(self):
        collector = RaceCollector(
            db_path=self.db_path,
            parse_race_detail_fn=lambda url, include_deep=False, debug=False: sample_race_data(url),
            extract_result_info_fn=lambda url, debug=False: sample_result(url),
        )
        try:
            collector.collect([SUCCESS_URL], progress_name="resume")
            summary = collector.collect([SUCCESS_URL], progress_name="resume")
            self.assertEqual(summary["success_count"], 1)
            self.assertGreaterEqual(summary["duplicate_count"], 1)
            self.assertEqual(collector.count_table("races"), 1)
            self.assertEqual(collector.count_table("race_entries"), 6)
        finally:
            collector.close()

    def test_retry_queue_resolves_after_successful_retry(self):
        state = {"fail_once": True}

        def flaky_parse(url, include_deep=False, debug=False):
            if url == FAIL_URL and state["fail_once"]:
                state["fail_once"] = False
                raise RuntimeError("temporary parse failure")
            return sample_race_data(url)

        collector = RaceCollector(
            db_path=self.db_path,
            parse_race_detail_fn=flaky_parse,
            extract_result_info_fn=lambda url, debug=False: sample_result(url),
        )
        try:
            first = collector.collect([FAIL_URL], progress_name="retry")
            self.assertEqual(first["failure_count"], 1)
            self.assertEqual(collector.pending_retry_count(), 1)

            second = collector.process_retry_queue(progress_name="retry")
            self.assertEqual(second["success_count"], 1)
            self.assertEqual(collector.pending_retry_count(), 0)
            self.assertEqual(collector.count_table("races"), 1)
        finally:
            collector.close()

    def test_data_quality_report_lists_missing_weather_and_entries(self):
        collector = RaceCollector(
            db_path=self.db_path,
            parse_race_detail_fn=lambda url, include_deep=False, debug=False: sample_race_data(url, entry_count=6),
            extract_result_info_fn=lambda url, debug=False: sample_result(url),
        )
        try:
            collector.collect([SUCCESS_URL], progress_name="quality")
            race_id = build_race_id(SUCCESS_URL)
            with collector.conn:
                collector.conn.execute("DELETE FROM weather WHERE race_id = ?", (race_id,))
                collector.conn.execute("DELETE FROM race_entries WHERE race_id = ? AND lane = 6", (race_id,))
            metrics = collect_quality_metrics(self.db_path, progress_name="quality")
            self.assertEqual(metrics["欠損データ件数"], 2)
            self.assertEqual(metrics["6艇揃っていないレース一覧"], [race_id])
            self.assertEqual(metrics["weather欠損一覧"], [race_id])
            markdown = render_markdown(metrics)
            self.assertIn("6艇揃っていないレース一覧", markdown)
            self.assertIn(race_id, markdown)
        finally:
            collector.close()


if __name__ == "__main__":
    unittest.main()

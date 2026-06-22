import datetime as dt
import json
import unittest
from collections import Counter, defaultdict
from pathlib import Path

import tennis_booker as tb


def sample_config() -> dict:
    return {
        "defaults": {
            "advance_days": 30,
            "open_time": "00:00:00",
            "lead_seconds": 1,
            "interval": 0.2,
            "max_attempts": 900,
            "payment_method": "EstateCredit",
            "validate": True,
            "book": False,
        },
        "facilities": [
            {
                "key": "tennis_court_3",
                "name": "Tennis Court 3",
                "facility_id": "court-3",
            }
        ],
        "bookings": [
            {"facility": "tennis_court_3", "date": "2026-07-23", "preferred_starts": ["08:00:00"]},
            {"facility": "tennis_court_3", "date": "2026-07-23", "preferred_starts": ["07:00:00"]},
            {"facility": "tennis_court_3", "date": "2026-07-26", "preferred_starts": ["07:00:00"]},
        ],
    }


class SchedulerSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs = tb.expand_config_jobs(sample_config())
        self.tz = self.jobs[0]["start_at"].tzinfo

    def at(self, value: str) -> dt.datetime:
        return dt.datetime.fromisoformat(value).replace(tzinfo=self.tz)

    def test_jobs_sort_by_date_then_start_time(self) -> None:
        self.assertEqual(
            [(job["date"], job["preferred_starts"]) for job in self.jobs],
            [
                ("2026-07-23", ("07:00:00",)),
                ("2026-07-23", ("08:00:00",)),
                ("2026-07-26", ("07:00:00",)),
            ],
        )

    def test_due_window_selects_both_test_jobs(self) -> None:
        selected = tb.select_config_jobs(
            self.jobs,
            now=self.at("2026-06-22T23:59:00"),
            due_window_seconds=120,
        )
        self.assertEqual(selected["pending_before_shard"], 2)
        self.assertEqual([job["preferred_starts"] for job in selected["pending"]], [("07:00:00",), ("08:00:00",)])
        self.assertEqual([job["date"] for job in selected["future"]], ["2026-07-26"])

    def test_job_index_zero_selects_7am(self) -> None:
        selected = tb.select_config_jobs(
            self.jobs,
            now=self.at("2026-06-22T23:59:00"),
            due_window_seconds=120,
            job_index=0,
        )
        self.assertEqual(selected["pending_before_shard"], 2)
        self.assertEqual(len(selected["pending"]), 1)
        self.assertEqual(selected["pending"][0]["preferred_starts"], ("07:00:00",))

    def test_job_index_one_selects_8am(self) -> None:
        selected = tb.select_config_jobs(
            self.jobs,
            now=self.at("2026-06-22T23:59:00"),
            due_window_seconds=120,
            job_index=1,
        )
        self.assertEqual(selected["pending_before_shard"], 2)
        self.assertEqual(len(selected["pending"]), 1)
        self.assertEqual(selected["pending"][0]["preferred_starts"], ("08:00:00",))

    def test_job_index_out_of_range_noops(self) -> None:
        selected = tb.select_config_jobs(
            self.jobs,
            now=self.at("2026-06-22T23:59:00"),
            due_window_seconds=120,
            job_index=2,
        )
        self.assertEqual(selected["pending_before_shard"], 2)
        self.assertEqual(selected["pending"], [])

    def test_before_due_window_jobs_are_future(self) -> None:
        selected = tb.select_config_jobs(
            self.jobs,
            now=self.at("2026-06-22T23:57:58"),
            due_window_seconds=120,
        )
        self.assertEqual(selected["pending"], [])
        self.assertEqual(len(selected["future"]), 3)

    def test_after_open_time_jobs_are_skipped(self) -> None:
        selected = tb.select_config_jobs(
            self.jobs,
            now=self.at("2026-06-23T00:00:01"),
            due_window_seconds=120,
        )
        self.assertEqual([job["date"] for job in selected["skipped"]], ["2026-07-23", "2026-07-23"])
        self.assertEqual(selected["pending"], [])


class TenYearConfigTest(unittest.TestCase):
    def test_sunday_config_has_independent_7am_and_8am_entries(self) -> None:
        config = json.loads(Path("sunday_8am_bookings_10y.json").read_text())
        bookings = config["bookings"]
        self.assertTrue(bookings)
        self.assertTrue(all(len(entry["preferred_starts"]) == 1 for entry in bookings))

        starts = Counter(tuple(entry["preferred_starts"]) for entry in bookings)
        self.assertEqual(starts[("07:00:00",)], starts[("08:00:00",)])
        self.assertEqual(set(starts), {("07:00:00",), ("08:00:00",)})

        by_date = defaultdict(set)
        for entry in bookings:
            self.assertEqual(entry["facility"], "tennis_court_3")
            by_date[entry["date"]].add(entry["preferred_starts"][0])
        self.assertTrue(all(starts_for_date == {"07:00:00", "08:00:00"} for starts_for_date in by_date.values()))


if __name__ == "__main__":
    unittest.main()

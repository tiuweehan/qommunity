from __future__ import annotations

import datetime as dt
import json
import unittest
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import tennis_booker as tb


class FakeResponse:
    def __init__(self, body: dict, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code
        self.headers = {}
        self.content = json.dumps(body).encode()
        self.text = json.dumps(body)

    def json(self) -> dict:
        return self._body


class FakeSession:
    no_color = True

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.requests.append((method, url))
        if not self.responses:
            raise AssertionError(f"Unexpected request {method} {url}")
        return FakeResponse(self.responses.pop(0))


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

    def test_jobs_due_today_selects_tonight_jobs(self) -> None:
        selected = tb.select_jobs_due_today(self.jobs, now=self.at("2026-06-22T08:00:00"))
        self.assertEqual([job["preferred_starts"] for job in selected], [("07:00:00",), ("08:00:00",)])

    def test_jobs_due_today_skips_after_midnight(self) -> None:
        selected = tb.select_jobs_due_today(self.jobs, now=self.at("2026-06-23T00:00:01"))
        self.assertEqual(selected, [])

    def test_earliest_not_yet_open_date_uses_day_or_slot_status(self) -> None:
        data = {
            "availability": {
                "availableDates": [
                    {"date": "2026-07-23", "status": "Available", "timeSlots": []},
                    {"date": "2026-07-24", "status": "Available", "timeSlots": [{"status": "Not Yet Open"}]},
                    {"date": "2026-07-25", "status": "Not Yet Open", "timeSlots": []},
                ]
            }
        }
        self.assertEqual(tb.earliest_not_yet_open_date(data), "2026-07-24")

    def test_dynamic_open_time_is_next_configured_open_time(self) -> None:
        job = self.jobs[0]
        updated = tb.with_dynamic_open_times(job, now=self.at("2026-06-22T08:00:00"))

        self.assertEqual(updated["open_at"].isoformat(timespec="seconds"), "2026-06-23T00:00:00+08:00")
        self.assertEqual(updated["start_at"].isoformat(timespec="seconds"), "2026-06-22T23:59:59+08:00")
        self.assertEqual(updated["due_source"], "earliest_not_yet_open")
        self.assertEqual(tb.actual_advance_days(updated), 30)


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


class TelegramMessageTest(unittest.TestCase):
    def test_exception_debug_message_includes_traceback_context(self) -> None:
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            message = tb.format_exception_debug_message("Unhandled tennis_booker.py exception", exc, extra="job: test")

        self.assertIn("Unhandled tennis_booker.py exception", message)
        self.assertIn("type: RuntimeError", message)
        self.assertIn("error: boom", message)
        self.assertIn("argv:", message)
        self.assertIn("job: test", message)
        self.assertIn("traceback:", message)
        self.assertIn("RuntimeError: boom", message)

    def test_otp_message_can_include_otp(self) -> None:
        at = dt.datetime.fromisoformat("2026-06-21T22:05:25+08:00")

        message = tb.format_otp_login_message("OTP received", "email", "deantiu56@gmail.com", at, otp="123456")

        self.assertEqual(
            message,
            "\n".join(
                [
                    "OTP received",
                    "mode: email",
                    "contact: deantiu56@gmail.com",
                    "otp: 123456",
                    "at: 2026-06-21T22:05:25+08:00",
                ]
            ),
        )

    def test_start_message_is_human_readable(self) -> None:
        job = tb.expand_config_jobs(sample_config())[1]

        message = tb.format_booking_start_message(job, job_index=1)

        self.assertIn("<b>🎾 Booking About To Run</b>", message)
        self.assertIn("Facility: Tennis Court 3", message)
        self.assertIn("Slot: 08:00 AM to 09:00 AM", message)
        self.assertIn("Date: 2026-07-23 (Thu)", message)
        self.assertIn("Job: 1", message)
        self.assertNotIn("Opens:", message)
        self.assertNotIn("Starts:", message)
        self.assertNotIn("Booking Enabled:", message)

    def test_tonight_jobs_message_lists_due_bookings(self) -> None:
        jobs = tb.expand_config_jobs(sample_config())[:2]
        jobs[0]["fee"] = Decimal("3.27")
        jobs[1]["fee"] = Decimal("3.27")
        now = dt.datetime.fromisoformat("2026-06-22T08:00:00+08:00")
        auth_expires_at = dt.datetime.fromisoformat("2026-06-30T09:12:34+08:00")

        message = tb.format_tonight_jobs_message(
            jobs,
            now,
            auth_ok=True,
            auth_expires_at=auth_expires_at,
            credit_before=Decimal("37.78"),
        )

        self.assertIn("<b>📅 Bookings Due Tonight</b>", message)
        self.assertIn("Run Date: 2026-06-22 (Mon)", message)
        self.assertIn("Auth: 2026-06-30 09:12:34 ✅", message)
        self.assertIn("Credit: $37.78 -> $31.24 ✅", message)
        self.assertIn("Advance: 30 days", message)
        self.assertIn("Count: 2", message)
        self.assertLess(message.index("Advance: 30 days"), message.index("Count: 2"))
        self.assertIn("1. Tennis Court 3", message)
        self.assertIn("Slot: 07:00 AM to 08:00 AM", message)
        self.assertIn("Fee: $3.27", message)
        self.assertIn("Job: 0", message)
        self.assertIn("2. Tennis Court 3", message)
        self.assertIn("Slot: 08:00 AM to 09:00 AM", message)
        self.assertIn("Job: 1", message)
        self.assertIn("Date: 2026-07-23 (Thu)", message)

    def test_success_message_includes_timeline_and_booking_id(self) -> None:
        job = tb.expand_config_jobs(sample_config())[0]
        tz = job["start_at"].tzinfo
        report = {
            "slot_start": "07:00:00",
            "slot_end": "08:00:00",
            "checks": [
                {"at": dt.datetime(2026, 6, 22, 23, 59, 59, 4000, tzinfo=tz), "status": "Not Yet Open"},
                {"at": dt.datetime(2026, 6, 22, 23, 59, 59, 557000, tzinfo=tz), "status": "Not Yet Open"},
                {"at": dt.datetime(2026, 6, 23, 0, 0, 0, 227000, tzinfo=tz), "status": "Available"},
            ],
            "sent_at": dt.datetime(2026, 6, 23, 0, 0, 0, 227000, tzinfo=tz),
            "ack_at": dt.datetime(2026, 6, 23, 0, 0, 35, 196000, tzinfo=tz),
            "duration_seconds": 30.036,
            "timed_out": True,
            "booking_id": "e9c4fc0e-50b6-416b-98ba-6354f9581297",
        }

        message = tb.format_booking_result_message(True, job, report, attempts=3, job_index=0)

        self.assertIn("<b>✅ Booking Succeeded</b>", message)
        self.assertIn("Facility: Tennis Court 3", message)
        self.assertIn("Slot: 07:00 AM to 08:00 AM", message)
        self.assertIn("Date: 2026-07-23 (Thu)", message)
        self.assertIn("Job: 0", message)
        self.assertIn("Checks: 3", message)
        self.assertIn("  - 23:59:59.004: Not Yet Open", message)
        self.assertIn("  - 00:00:00.227: Available", message)
        self.assertIn("Sent: 00:00:00.227", message)
        self.assertIn("Acknowledged: 00:00:35.196", message)
        self.assertIn("Duration: 30.036s", message)
        self.assertIn("Timeout: Yes", message)
        self.assertIn("Booking ID: e9c4fc0e-50b6-416b-98ba-6354f9581297", message)
        self.assertNotIn("Failed Reason:", message)

    def test_failure_message_includes_reason(self) -> None:
        job = tb.expand_config_jobs(sample_config())[0]
        message = tb.format_booking_result_message(
            False,
            job,
            {"checks": [], "timed_out": False},
            attempts=1,
            failure_reason="slot is already full",
            job_index=1,
        )

        self.assertIn("<b>❌ Booking Failed</b>", message)
        self.assertIn("Job: 1", message)
        self.assertIn("Failed Reason: slot is already full", message)


class FinancialsTest(unittest.TestCase):
    def test_fetch_estate_credit_balance_reads_app_total_balance(self) -> None:
        session = FakeSession(
            [
                {
                    "estateCreditOutput": {
                        "totalBalance": "37.7800",
                        "estateCreditBalanceOutputs": [{"balance": "10.0000"}],
                    }
                }
            ]
        )

        self.assertEqual(tb.fetch_estate_credit_balance(session), Decimal("37.7800"))

    def test_fetch_booking_fee_from_history_matches_facility_and_slot(self) -> None:
        job = tb.expand_config_jobs(sample_config())[0]
        session = FakeSession(
            [
                {
                    "paginatedList": {
                        "items": [
                            {
                                "facilityId": "court-3",
                                "facilityName": "Tennis Court 3",
                                "bookingFee": "3.2700",
                                "timeSlots": [{"startTime": "07:00:00", "endTime": "08:00:00"}],
                            }
                        ],
                        "totalPages": 1,
                    }
                }
            ]
        )

        self.assertEqual(tb.fetch_booking_fee_from_history(session, job), Decimal("3.2700"))


if __name__ == "__main__":
    unittest.main()

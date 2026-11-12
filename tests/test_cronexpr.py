import contextlib
import io
import json
import os
import unittest
from datetime import datetime
from unittest import mock

import cronexpr as ce


def run_cli(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ce.main(args)
    return code, out.getvalue(), err.getvalue()


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class Parsing(unittest.TestCase):
    def test_all_stars(self):
        c = ce.parse("* * * * *")
        self.assertEqual(c.minutes, set(range(60)))
        self.assertEqual(c.hours, set(range(24)))
        self.assertEqual(c.doms, set(range(1, 32)))
        self.assertEqual(c.months, set(range(1, 13)))
        self.assertEqual(c.dows, set(range(7)))
        self.assertEqual(c.day_semantics, "and")

    def test_lists_ranges_steps(self):
        c = ce.parse("1,2,5-7,*/15 * * * *")
        self.assertEqual(c.minutes, {0, 1, 2, 5, 6, 7, 15, 30, 45})

    def test_range_with_step(self):
        c = ce.parse("1-30/5 * * * *")
        self.assertEqual(c.minutes, {1, 6, 11, 16, 21, 26})

    def test_start_step(self):
        c = ce.parse("5/15 * * * *")
        self.assertEqual(c.minutes, {5, 20, 35, 50})

    def test_month_names_case_insensitive(self):
        self.assertEqual(ce.parse("0 0 * jan,MAR,dec *").months, {1, 3, 12})
        self.assertEqual(ce.parse("0 0 * JAN-mar *").months, {1, 2, 3})

    def test_weekday_names(self):
        self.assertEqual(ce.parse("0 0 * * mon-FRI").dows, {1, 2, 3, 4, 5})
        self.assertEqual(ce.parse("0 0 * * Sun").dows, {0})

    def test_sunday_zero_and_seven(self):
        self.assertEqual(ce.parse("0 0 * * 0").dows, {0})
        self.assertEqual(ce.parse("0 0 * * 7").dows, {0})
        self.assertEqual(ce.parse("0 0 * * 5-7").dows, {5, 6, 0})

    def test_wrong_field_count(self):
        for expr in ("* * * *", "* * * * * *"):
            with self.assertRaises(ce.CronError) as ctx:
                ce.parse(expr)
            self.assertIn("5 fields", str(ctx.exception))

    def test_out_of_range(self):
        for expr in ("60 * * * *", "* 24 * * *", "* * 0 * *", "* * 32 * *",
                     "* * * 13 *", "* * * 0 *", "* * * * 8"):
            with self.assertRaises(ce.CronError, msg=expr):
                ce.parse(expr)

    def test_bad_step(self):
        for expr in ("*/0 * * * *", "*/x * * * *", "1-5/0 * * * *", "*/2/3 * * * *"):
            with self.assertRaises(ce.CronError, msg=expr):
                ce.parse(expr)

    def test_reversed_range(self):
        with self.assertRaises(ce.CronError) as ctx:
            ce.parse("5-1 * * * *")
        self.assertIn("reversed range", str(ctx.exception))

    def test_empty_items(self):
        for expr in ("1,,2 * * * *", "1, * * * *", "", "   "):
            with self.assertRaises(ce.CronError, msg=repr(expr)):
                ce.parse(expr)

    def test_unknown_names(self):
        for expr in ("0 0 * FOO *", "0 0 * * XYZ", "JAN * * * *"):
            with self.assertRaises(ce.CronError, msg=expr):
                ce.parse(expr)

    def test_quartz_rejected(self):
        for expr in ("0 0 ? * *", "0 0 L * *", "0 0 15W * *", "0 0 * * 6#3"):
            with self.assertRaises(ce.CronError, msg=expr):
                ce.parse(expr)

    def test_nickname_rejected(self):
        with self.assertRaises(ce.CronError) as ctx:
            ce.parse("@daily")
        self.assertIn("not supported", str(ctx.exception))

    def test_malformed_tokens(self):
        for expr in ("1--2 * * * *", "1-2-3 * * * *", "-5 * * * *",
                     "1.5 * * * *", "5- * * * *"):
            with self.assertRaises(ce.CronError, msg=expr):
                ce.parse(expr)


class Explain(unittest.TestCase):
    def test_every_minute(self):
        self.assertEqual(ce.describe(ce.parse("* * * * *")), ["Every minute."])

    def test_fixed_time(self):
        self.assertEqual(ce.describe(ce.parse("30 4 * * *")), ["At 04:30."])

    def test_business_quarters(self):
        lines = ce.describe(ce.parse("*/15 9-17 * * MON-FRI"))
        self.assertEqual(lines, ["Every 15 minutes, between 09:00 and 17:59, "
                                 "on Monday through Friday."])

    def test_or_note(self):
        lines = ce.describe(ce.parse("0 0 1 * MON"))
        self.assertEqual(len(lines), 2)
        self.assertIn("OR", lines[1])
        self.assertEqual(len(ce.describe(ce.parse("0 0 * * MON"))), 1)

    def test_month_range(self):
        self.assertEqual(ce.describe(ce.parse("0 12 * JAN-MAR *")),
                         ["At 12:00, in January through March."])


class Next(unittest.TestCase):
    START = datetime(2026, 11, 11, 8, 50)  # a Wednesday

    def test_every_minute(self):
        times = ce.next_runs(ce.parse("* * * * *"), self.START, 3)
        self.assertEqual(times, [datetime(2026, 11, 11, 8, 51),
                                 datetime(2026, 11, 11, 8, 52),
                                 datetime(2026, 11, 11, 8, 53)])

    def test_from_is_exclusive(self):
        times = ce.next_runs(ce.parse("0 9 * * *"), datetime(2026, 11, 11, 9, 0), 1)
        self.assertEqual(times, [datetime(2026, 11, 12, 9, 0)])

    def test_business_quarters(self):
        times = ce.next_runs(ce.parse("*/15 9-17 * * MON-FRI"), self.START, 5)
        self.assertEqual(times, [datetime(2026, 11, 11, 9, 0),
                                 datetime(2026, 11, 11, 9, 15),
                                 datetime(2026, 11, 11, 9, 30),
                                 datetime(2026, 11, 11, 9, 45),
                                 datetime(2026, 11, 11, 10, 0)])

    def test_weekday_skip(self):
        times = ce.next_runs(ce.parse("0 9 * * MON"), self.START, 1)
        self.assertEqual(times, [datetime(2026, 11, 16, 9, 0)])

    def test_dom_dow_or(self):
        times = ce.next_runs(ce.parse("0 0 1 * MON"), self.START, 4)
        self.assertEqual(times, [datetime(2026, 11, 16, 0, 0),  # Monday
                                 datetime(2026, 11, 23, 0, 0),  # Monday
                                 datetime(2026, 11, 30, 0, 0),  # Monday
                                 datetime(2026, 12, 1, 0, 0)])  # 1st, a Tuesday

    def test_dom_only(self):
        times = ce.next_runs(ce.parse("0 0 13 * *"), self.START, 2)
        self.assertEqual(times, [datetime(2026, 11, 13, 0, 0),
                                 datetime(2026, 12, 13, 0, 0)])

    def test_year_rollover(self):
        times = ce.next_runs(ce.parse("0 0 1 1 *"), self.START, 1)
        self.assertEqual(times, [datetime(2027, 1, 1, 0, 0)])

    def test_leap_day(self):
        times = ce.next_runs(ce.parse("0 0 29 2 *"), self.START, 1)
        self.assertEqual(times, [datetime(2028, 2, 29, 0, 0)])

    def test_never_fires(self):
        with self.assertRaises(ce.CronError):
            ce.next_runs(ce.parse("0 0 30 2 *"), self.START, 1)

    def test_horizon_overflow(self):
        with self.assertRaises(ce.CronError):
            ce.next_runs(ce.parse("* * * * *"), datetime(9999, 12, 31, 23, 59), 1)


class Cli(unittest.TestCase):
    def test_validate_ok(self):
        code, out, _ = run_cli(["validate", "*/5 * * * *", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("valid", out)

    def test_validate_invalid(self):
        code, out, _ = run_cli(["validate", "61 * * * *", "--no-color"])
        self.assertEqual(code, 1)
        self.assertIn("out of range", out)

    def test_explain(self):
        code, out, _ = run_cli(["explain", "30 4 * * *", "--no-color"])
        self.assertEqual(code, 0)
        self.assertIn("At 04:30.", out)

    def test_explain_invalid_expression(self):
        code, _, err = run_cli(["explain", "nonsense"])
        self.assertEqual(code, 1)
        self.assertIn("invalid expression", err)

    def test_next_json(self):
        code, out, _ = run_cli(["next", "*/15 9-17 * * MON-FRI",
                                "--from", "2026-11-11 08:50", "--count", "3", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["times"], ["2026-11-11 09:00", "2026-11-11 09:15",
                                         "2026-11-11 09:30"])
        self.assertEqual(data["from"], "2026-11-11 08:50")

    def test_bad_from_exit2(self):
        code, _, err = run_cli(["next", "* * * * *", "--from", "11/11/2026"])
        self.assertEqual(code, 2)
        self.assertIn("bad --from", err)

    def test_bad_count_exit2(self):
        code, _, err = run_cli(["next", "* * * * *", "--count", "0"])
        self.assertEqual(code, 2)
        self.assertIn("--count", err)

    def test_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
            ce.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("cronexpr 1.0.0", out.getvalue())

    def test_json_flag_before_and_after_subcommand(self):
        for args in (["--json", "validate", "* * * * *"],
                     ["validate", "* * * * *", "--json"]):
            code, out, _ = run_cli(args)
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out)["valid"])

    def test_explain_json_fields(self):
        code, out, _ = run_cli(["explain", "*/15 9-17 * * MON-FRI", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["day_semantics"], "and")
        self.assertEqual(data["fields"][0]["values"], [0, 15, 30, 45])
        self.assertEqual(data["fields"][4]["values"], [1, 2, 3, 4, 5])

    def test_color_gating(self):
        out = FakeTTY()
        with contextlib.redirect_stdout(out), mock.patch.dict(os.environ):
            os.environ.pop("NO_COLOR", None)
            ce.main(["explain", "30 4 * * *"])
        self.assertIn("\x1b[", out.getvalue())
        out = FakeTTY()
        with contextlib.redirect_stdout(out), mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            ce.main(["explain", "30 4 * * *"])
        self.assertNotIn("\x1b[", out.getvalue())
        out = FakeTTY()
        with contextlib.redirect_stdout(out):
            ce.main(["explain", "30 4 * * *", "--no-color"])
        self.assertNotIn("\x1b[", out.getvalue())


if __name__ == "__main__":
    unittest.main()
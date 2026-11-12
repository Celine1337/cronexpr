#!/usr/bin/env python3
"""cronexpr - parse, explain and preview standard 5-field cron expressions.

Understands '*', lists, ranges, steps, month names JAN-DEC and weekday names
SUN-SAT (0 and 7 are both Sunday). Subcommands: explain, next, validate.
When day-of-month and day-of-week are both restricted they are OR-ed, as in
standard (Vixie) cron. Single file, standard library only.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta

__version__ = "1.0.0"

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]
DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday"]
MONTH_ABBR = {m[:3].upper(): i + 1 for i, m in enumerate(MONTH_NAMES)}
DAY_ABBR = {d[:3].upper(): i for i, d in enumerate(DAY_NAMES)}

# name, lo, hi, names, range hint
FIELDS = (
    ("minute", 0, 59, None, "0-59"),
    ("hour", 0, 23, None, "0-23"),
    ("day-of-month", 1, 31, None, "1-31"),
    ("month", 1, 12, MONTH_ABBR, "1-12 or JAN-DEC"),
    ("day-of-week", 0, 7, DAY_ABBR, "0-7 or SUN-SAT (0 and 7 are Sunday)"),
)
FULL_DOM = set(range(1, 32))
FULL_DOW = set(range(0, 7))
SEARCH_DAYS = 366 * 8  # next: give up after ~8 years (covers leap-day schedules)


class CronError(ValueError):
    """Invalid cron expression or unsatisfiable schedule."""


class CronExpr:
    def __init__(self, text, raw, minutes, hours, doms, months, dows):
        self.text = text
        self.raw = raw
        self.minutes = minutes
        self.hours = hours
        self.doms = doms
        self.months = months
        self.dows = dows

    @property
    def dom_restricted(self):
        return self.doms != FULL_DOM

    @property
    def dow_restricted(self):
        return self.dows != FULL_DOW

    @property
    def day_semantics(self):
        return "or" if (self.dom_restricted and self.dow_restricted) else "and"

    def day_matches(self, dt):
        dow = (dt.weekday() + 1) % 7  # Python Monday=0 -> cron Sunday=0
        if self.day_semantics == "or":
            return dt.day in self.doms or dow in self.dows
        return dt.day in self.doms and dow in self.dows

    def fields_dict(self):
        sets = (self.minutes, self.hours, self.doms, self.months, self.dows)
        return [{"name": f[0], "raw": r, "values": sorted(s)}
                for f, r, s in zip(FIELDS, self.raw, sets)]


def _value(token, name, lo, hi, names, hint):
    if names and token.upper() in names:
        return names[token.upper()]
    if re.fullmatch(r"[0-9]+", token):
        v = int(token)
        if v < lo or v > hi:
            raise CronError("%s: '%s' out of range %s" % (name, token, hint))
        return v
    raise CronError("%s: invalid value '%s' (expected %s)" % (name, token, hint))


def _parse_field(raw, spec):
    name, lo, hi, names, hint = spec
    values = set()
    for item in raw.split(","):
        if not item:
            raise CronError("%s: empty list item in '%s'" % (name, raw))
        base, slash, step_s = item.partition("/")
        if slash:
            if not re.fullmatch(r"[0-9]+", step_s):
                raise CronError("%s: invalid step '%s' in '%s' (expected a positive integer)"
                                % (name, step_s, item))
            step = int(step_s)
            if step < 1:
                raise CronError("%s: step must be >= 1 in '%s'" % (name, item))
        else:
            step = 1
        if re.fullmatch(r"[0-9]*[LW][0-9]*", base.replace("-", "")):
            raise CronError("%s: Quartz-style extension '%s' is not supported "
                            "(only standard 5-field cron)" % (name, item))
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, dash, b = base.partition("-")
            if not dash or "-" in b or not a or not b:
                raise CronError("%s: malformed range '%s'" % (name, base))
            start = _value(a, name, lo, hi, names, hint)
            end = _value(b, name, lo, hi, names, hint)
            if start > end:
                raise CronError("%s: reversed range '%s' (start must be <= end)" % (name, base))
        else:
            start = _value(base, name, lo, hi, names, hint)
            end = hi if slash else start
        values.update(range(start, end + 1, step))
    if name == "day-of-week" and 7 in values:
        values.discard(7)
        values.add(0)
    return values


def parse(text):
    """Parse a 5-field cron expression; raise CronError with a clear reason."""
    if not isinstance(text, str) or not text.strip():
        raise CronError("empty expression (expected 5 fields: minute hour "
                        "day-of-month month day-of-week)")
    text = text.strip()
    if text.startswith("@"):
        raise CronError("nickname '%s' is not supported; write the equivalent "
                        "5-field expression" % text.split()[0])
    if "?" in text or "#" in text:
        raise CronError("Quartz-style extensions (?, #) are not supported "
                        "(only standard 5-field cron)")
    raw = text.split()
    if len(raw) != 5:
        raise CronError("expected 5 fields (minute hour day-of-month month "
                        "day-of-week), got %d%s" % (
                            len(raw),
                            " (seconds/year fields are not supported)" if len(raw) > 5 else ""))
    sets = [_parse_field(r, spec) for r, spec in zip(raw, FIELDS)]
    return CronExpr(text, raw, *sets)


def next_runs(cron, start, count):
    """Next `count` run times strictly after `start` (minute resolution)."""
    try:
        cur = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    except OverflowError:
        raise CronError("no run time found within the search horizon (8 years)")
    try:
        limit = start + timedelta(days=SEARCH_DAYS)
    except OverflowError:
        limit = datetime.max
    out = []
    try:
        while len(out) < count:
            if cur > limit:
                raise CronError("no run time found within %d years - the schedule "
                                "may never fire (e.g. '0 0 30 2 *')" % (SEARCH_DAYS // 366))
            if cur.month not in cron.months:
                if cur.month == 12:
                    cur = cur.replace(year=cur.year + 1, month=1, day=1, hour=0, minute=0)
                else:
                    cur = cur.replace(month=cur.month + 1, day=1, hour=0, minute=0)
                continue
            if not cron.day_matches(cur):
                cur = (cur + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if cur.hour not in cron.hours:
                cur = cur.replace(minute=0) + timedelta(hours=1)
                continue
            if cur.minute not in cron.minutes:
                cur += timedelta(minutes=1)
                continue
            out.append(cur)
            cur += timedelta(minutes=1)
    except OverflowError:
        raise CronError("no run time found within the search horizon (8 years)")
    return out


def _runs(values):
    runs = []
    for v in sorted(values):
        if runs and v == runs[-1][1] + 1:
            runs[-1][1] = v
        else:
            runs.append([v, v])
    return runs


def _join(parts):
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _nums(values, fmt=str):
    return _join([fmt(a) if a == b else "%s through %s" % (fmt(a), fmt(b))
                  for a, b in _runs(values)])


def _step_of(values, lo, hi):
    vals = sorted(values)
    if len(vals) < 2 or vals[0] != lo:
        return None
    step = vals[1] - vals[0]
    if step >= 1 and vals == list(range(lo, hi + 1, step)):
        return step
    return None


def _time_phrase(minutes, hours):
    all_m = minutes == set(range(60))
    all_h = hours == set(range(24))
    hh = lambda h: "%02d" % h
    if all_m and all_h:
        return "Every minute"
    if all_m:
        if len(hours) == 1:
            return "Every minute during hour %02d" % next(iter(hours))
        return "Every minute during hours " + _nums(hours, hh)
    step = _step_of(minutes, 0, 59)
    if all_h:
        if step:
            return "Every %d minutes" % step
        if len(minutes) == 1:
            return "At minute %d of every hour" % next(iter(minutes))
        return "At minutes " + _nums(minutes) + " of every hour"
    times = sorted((h, m) for h in hours for m in minutes)
    if len(times) <= 8:
        return "At " + _join(["%02d:%02d" % t for t in times])
    if step and len(_runs(hours)) == 1:
        return "Every %d minutes, between %02d:00 and %02d:59" % (step, min(hours), max(hours))
    return "At minutes " + _nums(minutes) + " during hours " + _nums(hours, hh)


def describe(cron):
    """Human-readable sentence(s) for a parsed expression."""
    parts = [_time_phrase(cron.minutes, cron.hours)]
    if cron.dom_restricted:
        step = _step_of(cron.doms, 1, 31)
        if len(cron.doms) == 1:
            parts.append("on day %d of the month" % next(iter(cron.doms)))
        elif step:
            parts.append("every %d days of the month" % step)
        else:
            parts.append("on days " + _nums(cron.doms) + " of the month")
    if cron.months != set(range(1, 13)):
        parts.append("in " + _nums(cron.months, lambda m: MONTH_NAMES[m - 1]))
    if cron.dow_restricted:
        parts.append("on " + _nums(cron.dows, lambda d: DAY_NAMES[d]))
    lines = [", ".join(parts) + "."]
    if cron.day_semantics == "or":
        lines.append("Day-of-month and day-of-week are both restricted: the job "
                     "runs when either matches (standard cron OR semantics).")
    return lines


def colorize(text, color, enabled):
    if not enabled:
        return text
    codes = {"red": "31", "green": "32", "yellow": "33", "cyan": "36", "bold": "1"}
    return "\x1b[%sm%s\x1b[0m" % (codes[color], text)


def explain_report(cron, color, as_json):
    lines = describe(cron)
    if as_json:
        return json.dumps({"expression": cron.text, "valid": True,
                           "description": lines, "day_semantics": cron.day_semantics,
                           "fields": cron.fields_dict()}, indent=2)
    out = [colorize(cron.text, "bold", color), "  " + lines[0]]
    for extra in lines[1:]:
        out.append("  " + colorize(extra, "yellow", color))
    return "\n".join(out)


def next_report(cron, start, times, color, as_json):
    fmt = "%Y-%m-%d %H:%M"
    if as_json:
        return json.dumps({"expression": cron.text, "from": start.strftime(fmt),
                           "count": len(times),
                           "times": [t.strftime(fmt) for t in times]}, indent=2)
    out = [colorize(cron.text, "bold", color),
           "next %d runs from %s:" % (len(times), start.strftime(fmt))]
    for t in times:
        out.append("  %s  %s" % (colorize(t.strftime(fmt), "green", color), DAY_NAMES[(t.weekday() + 1) % 7][:3]))
    return "\n".join(out)


def validate_report(text, ok, reason, color, as_json):
    if as_json:
        return json.dumps({"expression": text, "valid": ok, "reason": reason}, indent=2)
    if ok:
        return "%s: %s" % (colorize("valid", "green", color), text)
    return "%s: %s\n  reason: %s" % (colorize("invalid", "red", color), text, reason)


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="machine-readable JSON output")
    common.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                        help="disable ANSI colors (also honors NO_COLOR)")
    parser = argparse.ArgumentParser(
        prog="cronexpr",
        description="Parse, explain and preview standard 5-field cron expressions.")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable JSON output")
    parser.add_argument("--no-color", action="store_true",
                        help="disable ANSI colors (also honors NO_COLOR)")
    parser.add_argument("--version", action="version", version="cronexpr " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_exp = sub.add_parser("explain", parents=[common],
                           help="human-readable description of an expression")
    p_exp.add_argument("expression")
    p_next = sub.add_parser("next", parents=[common], help="next run times")
    p_next.add_argument("expression")
    p_next.add_argument("--count", type=int, default=5, metavar="N",
                        help="how many run times (1-1000, default 5)")
    p_next.add_argument("--from", dest="start", default=None, metavar="TIME",
                        help="start time 'YYYY-MM-DD HH:MM' (default: now, local)")
    p_val = sub.add_parser("validate", parents=[common],
                           help="exit 0 if valid, 1 with a reason if not")
    p_val.add_argument("expression")
    return parser


def main(argv=None):
    opts = build_parser().parse_args(argv)
    color = not opts.no_color and not os.environ.get("NO_COLOR") and sys.stdout.isatty()
    try:
        cron = parse(opts.expression)
    except CronError as exc:
        if opts.command == "validate":
            print(validate_report(opts.expression, False, str(exc), color, opts.json))
        else:
            sys.stderr.write("cronexpr: invalid expression: %s\n" % exc)
        return 1
    if opts.command == "validate":
        print(validate_report(opts.expression, True, None, color, opts.json))
        return 0
    if opts.command == "explain":
        print(explain_report(cron, color, opts.json))
        return 0
    if not 1 <= opts.count <= 1000:
        sys.stderr.write("cronexpr: --count must be between 1 and 1000 (got %d)\n" % opts.count)
        return 2
    if opts.start is None:
        start = datetime.now()
    else:
        start = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                start = datetime.strptime(opts.start, fmt)
                break
            except ValueError:
                continue
        if start is None:
            sys.stderr.write("cronexpr: bad --from value '%s' (expected 'YYYY-MM-DD HH:MM')\n"
                             % opts.start)
            return 2
    try:
        times = next_runs(cron, start, opts.count)
    except CronError as exc:
        sys.stderr.write("cronexpr: %s\n" % exc)
        return 1
    print(next_report(cron, start, times, color, opts.json))
    return 0


if __name__ == "__main__":
    sys.exit(main())
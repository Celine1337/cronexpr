#!/usr/bin/env python3
"""Regenerate docs/explain.png and docs/next.png. Requires Pillow.

Run: python tools/make_shots.py
Renders the real CLI report pipeline on a tiny fixed fixture, fully offline.
"""
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import cronexpr as ce
import render_terminal as rt

EXPR = "*/15 9-17 * * MON-FRI"
FROM = datetime(2026, 11, 11, 8, 50)


def render(name, ansi, cmd, title):
    rt.render(rt.parse_ansi(ansi), title, cmd, os.path.join(ROOT, "docs", name + ".png"))


def main():
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    cron = ce.parse(EXPR)
    render("explain", ce.explain_report(cron, True, False),
           'python cronexpr.py explain "%s"' % EXPR, "cronexpr — explain")
    times = ce.next_runs(cron, FROM, 5)
    render("next", ce.next_report(cron, FROM, times, True, False),
           'python cronexpr.py next "%s" --from "2026-11-11 08:50"' % EXPR,
           "cronexpr — next")


if __name__ == "__main__":
    main()
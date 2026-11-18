"""CLI for the Stage 10 scheduler.

Usage:
  python -m scheduler                 # run all due jobs once
  python -m scheduler --job refresh_prices
  python -m scheduler --loop 300      # long-running loop
  python -m scheduler --list          # show job registry/status
  python -m scheduler --recover       # mark stale runs failed
"""
from __future__ import annotations

import argparse
import json

from scheduler.runner import Scheduler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scheduler", description="Portfolio automation scheduler")
    parser.add_argument("--job", help="run a single named job")
    parser.add_argument("--loop", type=int, metavar="SECONDS", help="run continuously every N seconds")
    parser.add_argument("--list", action="store_true", help="list registered jobs and status")
    parser.add_argument("--register", action="store_true", help="register configured jobs")
    parser.add_argument("--recover", action="store_true", help="recover stale running jobs")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    sched = Scheduler()
    if args.register:
        names = sched.register()
        print(json.dumps({"registered": names}) if args.json else f"registered: {names}")
        return 0
    if args.recover:
        n = sched.recover()
        print(json.dumps({"recovered": n}) if args.json else f"recovered {n} stale run(s)")
        return 0
    if args.list:
        jobs = sched.store.list_jobs()
        if args.json:
            print(json.dumps(jobs, indent=2, default=str))
        else:
            for j in jobs:
                print(f"{j['name']:<28} enabled={j['enabled']} last={j['last_status']} at {j['last_run_at']}")
        return 0
    if args.job:
        res = sched.run_job(args.job)
        print(json.dumps(res, indent=2, default=str) if args.json else res)
        return 0 if res.get("status") in ("success", "skipped") else 1
    if args.loop:
        sched.run_forever(interval_seconds=args.loop)
        return 0

    results = sched.run_due()
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for r in results:
            print(f"{r.get('job'):<28} {r.get('status')} rows={r.get('rows', 0)}")
    return 0 if all(r.get("status") in ("success", "skipped", "not_due") for r in results) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

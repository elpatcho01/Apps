"""Entry point for the research workflow: runs the requested steps against live
ONS data and writes each step's report to --out-dir, echoing it to stdout so
it can be read from the job log.

Steps are added here as workstreams land; an unknown step is an error rather
than a silent no-op.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

from . import probe, wedge, ws1, ws2a

STEPS = ("probe", "ws1", "wedge", "ws2a")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--step", default="all")
    ap.add_argument("--out-dir", default="out")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    steps = tuple(s for s in STEPS if s != "probe") if args.step == "all" else tuple(s.strip() for s in args.step.split(","))
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        print(f"unknown step(s): {unknown}; known: {STEPS}", file=sys.stderr)
        return 2
    if "probe" in steps:
        probe.main(["--out", str(out / "probe.json"),
                    "--checks", "price_quotes"])
    if "ws2a" in steps:
        ws2a.main(["--cache", "cache", "--out", str(out / "ws2a.json")])
    if "wedge" in steps:
        wedge.main(["--out", str(out / "wedge.json")])
    if "ws1" in steps:
        ws1.main(["--out", str(out / "ws1.json")])
    return 0


if __name__ == "__main__":
    sys.exit(main())

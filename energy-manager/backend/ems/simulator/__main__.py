"""CLI: python -m ems.simulator --config config/ems.example.yaml --start 2026-06-15 --days 2"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ems.control.base import NativeController
from ems.control.self_consumption import SelfConsumptionController
from ems.core.config import load_config
from ems.core.logging import setup_logging
from ems.simulator.runner import SimulationRunner

CONTROLLERS = {"self_consumption": SelfConsumptionController, "native": NativeController}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ems-sim", description="Draai het EMS tegen de simulator.")
    p.add_argument("--config", default=os.environ.get("EMS_CONFIG", "config/ems.example.yaml"))
    p.add_argument("--start", default=None, help="Startdatum (YYYY-MM-DD, lokale tijd); standaard vandaag")
    p.add_argument("--days", type=float, default=1.0)
    p.add_argument("--step", type=float, default=None, help="Simulatiestap in seconden (standaard regelinterval)")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--controller", choices=sorted(CONTROLLERS), default="self_consumption")
    p.add_argument("--compare-native", action="store_true", help="Vergelijk met 'zonder EMS'")
    p.add_argument("--out", default=None, help="Map voor CSV/JSON-uitvoer")
    p.add_argument("--show-decisions", type=int, default=5, help="Toon de laatste N EMS-beslissingen")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    tz = ZoneInfo(config.site.timezone)
    start_day = datetime.fromisoformat(args.start) if args.start else datetime.now(tz)
    start = start_day.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=tz)
    duration = timedelta(days=args.days)

    names = [args.controller] + (["native"] if args.compare_native and args.controller != "native" else [])
    summaries = {}
    for name in names:
        runner = SimulationRunner(config, start, controller=CONTROLLERS[name](), seed=args.seed, step_s=args.step)
        result = await runner.run(duration)
        summaries[name] = result.summary
        print(result.summary.render_nl())
        print()
        if args.out:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            result.write_csv(out / f"timeseries-{name}.csv")
            result.write_summary(out / f"summary-{name}.json")
        if args.show_decisions and result.journal and name == args.controller:
            entries = [e for e in result.journal.recent if e.outcome in ("sent", "dry_run")]
            if entries:
                print(f"Laatste {min(args.show_decisions, len(entries))} EMS-beslissingen:")
                for e in entries[-args.show_decisions:]:
                    print(e.render_nl(tz))
                print()
    if len(summaries) == 2:
        a, b = summaries[args.controller], summaries["native"]
        print(f"Vergelijking {args.controller} vs zonder EMS:")
        print(f"  Netafname      {a.import_kwh - b.import_kwh:+8.2f} kWh")
        print(f"  Teruglevering  {a.export_kwh - b.export_kwh:+8.2f} kWh")
        print(f"  Spotwaarde     {a.spot_value_eur - b.spot_value_eur:+8.2f} EUR (indicatief)")
        if abs(a.ev_kwh - b.ev_kwh) > 0.5:
            print(f"  Let op: auto geladen {a.ev_kwh:.1f} vs {b.ev_kwh:.1f} kWh — de vergelijking is niet gelijkwaardig")
            print("  zolang 'gewenste SOC bij vertrek' nog niet bestaat (EV-planning volgt in fase 12).")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    setup_logging("DEBUG" if args.verbose else "WARNING", json_output=False)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())

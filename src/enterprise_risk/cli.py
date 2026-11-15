import argparse
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from sqlalchemy.exc import SQLAlchemyError

from enterprise_risk.analytics.queries import counts, quality, summary
from enterprise_risk.db import get_engine
from enterprise_risk.ingestion.coinbase import SourceError
from enterprise_risk.ingestion.service import ingest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Public operational incident ingestion and analytics"
    )
    parser.add_argument(
        "command",
        choices=[
            "ingest",
            "counts",
            "summary",
            "quality",
            "market-ingest",
            "market-update",
            "market-indicators",
            "market-quality",
            "market-analysis",
            "market-report",
            "coverage-import",
            "market-render",
        ],
    )
    parser.add_argument("--products", nargs="+", default=["BTC-USD", "ETH-USD", "SOL-USD"])
    parser.add_argument("--start", type=date.fromisoformat, help="Inclusive UTC date")
    parser.add_argument(
        "--end", type=date.fromisoformat, help="Exclusive UTC date; capped at today"
    )
    parser.add_argument("--output", default="reports")
    parser.add_argument("--baseline-days", type=int, default=30)
    parser.add_argument("--quantile", type=float, default=0.95)
    parser.add_argument("--volume-z", type=float, default=2.0)
    parser.add_argument("--coverage-file", type=Path)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    engine = None
    try:
        if args.command == "market-render":
            from enterprise_risk.market.outputs import render_snapshot

            if args.snapshot is None:
                raise ValueError("market-render requires --snapshot")
            result = render_snapshot(args.snapshot, args.output)
            print(json.dumps(result, indent=2, default=str))
            return 2 if result["critical_failures"] else 0
        engine = get_engine()
        if args.command == "ingest":
            with httpx.Client(headers={"User-Agent": "enterprise-risk-intelligence/0.1"}) as client:
                result = ingest(engine, client)
        elif args.command.startswith("market-") or args.command == "coverage-import":
            result = market_command(engine, args)
        else:
            result = {"counts": counts, "summary": summary, "quality": quality}[args.command](
                engine
            )
        print(json.dumps(result, indent=2, default=str))
        return (
            2
            if (result.get("outcome", "success") != "success" or result.get("critical_failures", 0))
            else 0
        )
    except (SourceError, ValueError, OSError) as exc:
        logging.error("%s", exc)
        return 1
    except SQLAlchemyError:
        logging.error("Database operation failed; check connectivity and run Alembic migrations")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


def market_command(engine, args):
    from sqlalchemy.orm import Session

    from enterprise_risk.market.api import MarketAPI, product_id
    from enterprise_risk.market.indicators import IndicatorConfig, calculate_indicators
    from enterprise_risk.market.ingestion import default_start, ingest_market
    from enterprise_risk.market.research import (
        assess_coverage,
        load_data,
        quality,
        register_coverage,
    )

    products = list(dict.fromkeys(product_id(p) for p in args.products))
    config = IndicatorConfig(args.baseline_days, args.quantile, args.volume_z)
    end = min(args.end or datetime.now(UTC).date(), datetime.now(UTC).date())
    with Session(engine) as session:
        start = args.start or default_start(session, end)
    if start >= end:
        raise ValueError("Require start < finalized end")
    if args.command == "coverage-import":
        if args.coverage_file is None:
            raise ValueError("coverage-import requires --coverage-file with documentary evidence")
        return register_coverage(engine, json.loads(args.coverage_file.read_text(encoding="utf-8")))
    if args.command in {"market-ingest", "market-update"}:
        with httpx.Client(headers={"User-Agent": "enterprise-risk-intelligence/0.2"}) as client:
            return ingest_market(
                engine,
                MarketAPI(client),
                products,
                start,
                end,
                incremental=args.command == "market-update",
            )
    if args.command == "market-indicators":
        return calculate_indicators(engine, products, config)
    if args.command == "market-quality":
        from enterprise_risk.market.outputs import write_json

        data = load_data(engine, products, config.version, start, end)
        result = quality(data, products, start, end)
        result["incident_coverage"] = assess_coverage(
            data["incidents"], data["coverage"], start, end
        )
        Path(args.output).mkdir(parents=True, exist_ok=True)
        write_json(Path(args.output) / "quality_summary.json", result)
        return result
    from enterprise_risk.market.outputs import run_report

    return run_report(engine, products, start, end, args.output, config)


if __name__ == "__main__":
    raise SystemExit(main())

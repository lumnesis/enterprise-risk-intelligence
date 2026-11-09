import argparse
import json
import logging

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
    parser.add_argument("command", choices=["ingest", "counts", "summary", "quality"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    engine = None
    try:
        engine = get_engine()
        if args.command == "ingest":
            with httpx.Client(headers={"User-Agent": "enterprise-risk-intelligence/0.1"}) as client:
                result = ingest(engine, client)
        else:
            result = {"counts": counts, "summary": summary, "quality": quality}[args.command](
                engine
            )
        print(json.dumps(result, indent=2, default=str))
        return 2 if args.command == "ingest" and result["outcome"] != "success" else 0
    except (SourceError, ValueError) as exc:
        logging.error("%s", exc)
        return 1
    except SQLAlchemyError:
        logging.error("Database operation failed; check connectivity and run Alembic migrations")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

"""Calendar-aware rolling statistics; prior-only empirical stress thresholds."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from statistics import mean, stdev

import numpy as np
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from enterprise_risk.market.api import DAY, midnight
from enterprise_risk.models import MarketCandle, MarketRiskIndicator


@dataclass(frozen=True)
class IndicatorConfig:
    baseline_days: int = 30
    quantile: float = 0.95
    volume_z_threshold: float = 2.0
    annualize: bool = True

    def __post_init__(self):
        if (
            self.baseline_days < 30
            or not 0.5 < self.quantile < 1
            or not math.isfinite(self.volume_z_threshold)
            or self.volume_z_threshold <= 0
        ):
            raise ValueError(
                "Require baseline >=30, 0.5 < quantile < 1, and positive finite z threshold"
            )

    @property
    def version(self):
        digest = hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]
        return f"daily-v1-{digest}"


def calculate(candles: list[dict], config=IndicatorConfig()) -> list[dict]:
    if not candles:
        return []
    product = candles[0]["product_id"]
    data = {row["candle_start_utc"].astimezone(UTC).date(): row for row in candles}
    if len(data) != len(candles) or any(row["product_id"] != product for row in candles):
        raise ValueError("Calculate requires unique daily candles for one product")
    rows = {}
    scale = math.sqrt(365) if config.annualize else 1

    def history(day, key, n, *, prior=False):
        end = day - DAY if prior else day
        values = [rows.get(end - i * DAY, {}).get(key) for i in range(n)]
        return values if all(v is not None for v in values) else None

    for day, candle in sorted(data.items()):
        previous = data.get(day - DAY)
        close, volume = float(candle["close"]), float(candle["volume"])
        daily = float(candle["close"] / previous["close"] - 1) if previous else None
        log_return = math.log(close / float(previous["close"])) if previous else None
        row = {
            "product_id": product,
            "observation_date": day,
            "calculation_version": config.version,
            "daily_return": daily,
            "log_return": log_return,
            "absolute_return": abs(daily) if daily is not None else None,
            "volume_change": float(candle["volume"] / previous["volume"] - 1)
            if previous and previous["volume"] > 0
            else None,
            "volume_mean_30d": None,
            "volume_zscore_30d": None,
            "return_threshold": None,
            "volatility_threshold": None,
            "volume_zscore_threshold": config.volume_z_threshold,
            "calculated_at": datetime.now(UTC),
            "parameters": asdict(config),
        }
        rows[day] = row
        for n in (7, 30):
            values = history(day, "log_return", n)
            row[f"realized_volatility_{n}d"] = stdev(values) * scale if values else None
        volumes = [
            float(data[day - i * DAY]["volume"]) for i in range(1, 31) if day - i * DAY in data
        ]
        if len(volumes) == 30:
            row["volume_mean_30d"] = mean(volumes)
            sigma = stdev(volumes)
            row["volume_zscore_30d"] = (volume - mean(volumes)) / sigma if sigma > 0 else None
        for indicator, threshold in (
            ("absolute_return", "return_threshold"),
            ("realized_volatility_30d", "volatility_threshold"),
        ):
            baseline = history(day, indicator, config.baseline_days, prior=True)
            if baseline:
                row[threshold] = float(np.quantile(baseline, config.quantile, method="linear"))
        comparisons = [
            None if row[metric] is None or row[threshold] is None else row[metric] > row[threshold]
            for metric, threshold in (
                ("absolute_return", "return_threshold"),
                ("realized_volatility_30d", "volatility_threshold"),
                ("volume_zscore_30d", "volume_zscore_threshold"),
            )
        ]
        row["market_stress_flag"] = (
            True
            if any(c is True for c in comparisons)
            else False
            if all(c is False for c in comparisons)
            else None
        )
    return list(rows.values())


def calculate_indicators(engine, products, config=IndicatorConfig(), today=None):
    today = today or datetime.now(UTC).date()
    count = 0
    with Session(engine) as session, session.begin():
        session.execute(text("SELECT pg_advisory_xact_lock(721349002)"))
        for product in dict.fromkeys(products):
            candles = [
                dict(row)
                for row in session.execute(
                    select(MarketCandle.__table__).where(
                        MarketCandle.product_id == product,
                        MarketCandle.candle_start_utc < midnight(today),
                    )
                ).mappings()
            ]
            values = calculate(candles, config)
            session.execute(
                delete(MarketRiskIndicator).where(
                    MarketRiskIndicator.product_id == product,
                    MarketRiskIndicator.calculation_version == config.version,
                )
            )
            if values:
                session.execute(MarketRiskIndicator.__table__.insert(), values)
            count += len(values)
    return {"calculation_version": config.version, "rows": count, "parameters": asdict(config)}

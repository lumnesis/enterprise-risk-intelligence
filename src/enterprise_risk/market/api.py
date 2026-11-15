"""Coinbase Advanced Trade public daily candles. No authentication or paid API."""

import re
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from enterprise_risk.ingestion.coinbase import SourceError

SOURCE = "coinbase_advanced_trade"
BASE_URL = "https://api.coinbase.com/api/v3/brokerage/market/products"
DEFAULT_PRODUCTS = ("BTC-USD", "ETH-USD", "SOL-USD")
DAY = timedelta(days=1)


def midnight(value: date) -> datetime:
    return datetime.combine(value, datetime.min.time(), UTC)


def product_id(value: str) -> str:
    if not re.fullmatch(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}", value):
        raise ValueError("Product must be a BASE-QUOTE identifier such as BTC-USD")
    return value


def windows(start: date, end: date, days: int = 300):
    """Half-open UTC ranges; 300 is below the API's documented 350 candle maximum."""
    if start >= end or not 1 <= days <= 300:
        raise ValueError("Require start < end and window size between 1 and 300")
    while start < end:
        stop = min(start + timedelta(days=days), end)
        yield start, stop
        start = stop


def decimal_value(value: Any) -> Decimal:
    # Reject binary floating input and values PostgreSQL NUMERIC(38,18) would round.
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise ValueError("Expected exact decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal") from exc
    if not number.is_finite() or number.copy_abs() >= Decimal(10) ** 20:
        raise ValueError("Nonfinite or oversized number")
    digits = "".join(map(str, number.as_tuple().digits))
    trailing_zeros = len(digits) - len(digits.rstrip("0"))
    if number != 0 and number.as_tuple().exponent + trailing_zeros < -18:
        raise ValueError("Decimal exceeds 18 fractional digits")
    return number


def parse_candle(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Candle must be an object")
    try:
        stamp = raw["start"]
        if isinstance(stamp, bool) or not re.fullmatch(r"\d{1,12}", str(stamp)):
            raise ValueError("Invalid epoch seconds")
        start = datetime.fromtimestamp(int(stamp), UTC)
        if start != midnight(start.date()):
            raise ValueError("Daily candle is not at UTC midnight")
        values = {
            key: decimal_value(raw[key]) for key in ("open", "high", "low", "close", "volume")
        }
    except (KeyError, OverflowError, OSError) as exc:
        raise ValueError("Missing or invalid candle field") from exc
    if min(values[k] for k in ("open", "high", "low", "close")) <= 0 or values["volume"] < 0:
        raise ValueError("Prices must be positive and volume nonnegative")
    if not (
        values["low"]
        <= min(values["open"], values["close"])
        <= max(values["open"], values["close"])
        <= values["high"]
    ):
        raise ValueError("Invalid OHLC relationship")
    return {"candle_start_utc": start, "granularity": "ONE_DAY", **values, "raw": raw}


class MarketAPI:
    def __init__(
        self, client: httpx.Client, *, attempts: int = 4, pace: float = 0.2, sleep=time.sleep
    ):
        if not 1 <= attempts <= 6 or pace < 0:
            raise ValueError("Invalid retry or pacing configuration")
        self.client, self.attempts, self.pace, self.sleep = client, attempts, pace, sleep

    def get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(self.attempts):
            self.sleep(self.pace)
            delay = min(2**attempt, 16)
            try:
                response = self.client.get(f"{BASE_URL}/{path}", params=params, timeout=30)
            except httpx.RequestError as exc:
                if attempt == self.attempts - 1:
                    raise SourceError("Market request failed after bounded retries") from exc
            else:
                if response.status_code not in (429, 500, 502, 503, 504):
                    if response.is_error:
                        raise SourceError(f"Market API returned HTTP {response.status_code}")
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise SourceError("Market API returned malformed JSON") from exc
                    if not isinstance(payload, dict):
                        raise SourceError("Market API expected a JSON object")
                    return payload
                if attempt == self.attempts - 1:
                    raise SourceError(
                        f"Market API HTTP {response.status_code}: retry limit reached"
                    )
                if retry_after := response.headers.get("Retry-After"):
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(
                                delay,
                                (
                                    parsedate_to_datetime(retry_after) - datetime.now(UTC)
                                ).total_seconds(),
                            )
                        except (ValueError, TypeError):
                            pass
                    if delay > 60:
                        raise SourceError("Market Retry-After exceeds 60 seconds; rerun later")
            self.sleep(delay)
        raise AssertionError("Unreachable")

    def product(self, identifier: str) -> dict:
        value = self.get(product_id(identifier))
        if value.get("product_id") != identifier or not all(
            isinstance(value.get(k), str) and value[k]
            for k in ("base_currency_id", "quote_currency_id")
        ):
            raise SourceError("Invalid public product response")
        return {
            "product_id": identifier,
            "base_currency": value["base_currency_id"],
            "quote_currency": value["quote_currency_id"],
            "source": SOURCE,
            "verified_at": datetime.now(UTC),
            "raw": value,
        }

    def candles(self, identifier: str, start: date, end: date) -> list:
        payload = self.get(
            f"{product_id(identifier)}/candles",
            {
                "start": str(int(midnight(start).timestamp())),
                # Avoid relying on whether the server treats end as inclusive.
                "end": str(int(midnight(end).timestamp()) - 1),
                "granularity": "ONE_DAY",
                "limit": 350,
            },
        )
        if not isinstance(payload.get("candles"), list) or len(payload["candles"]) > 350:
            raise SourceError("Invalid candles response envelope or limit")
        return payload["candles"]

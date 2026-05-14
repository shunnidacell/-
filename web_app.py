from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from arb_bot import (
    Config,
    Opportunity,
    Quote,
    fetch_quote,
    find_opportunities,
    load_account_trading_fees,
    load_account_trading_fees_sync,
    load_config,
    load_markets_and_fees,
    make_sync_exchange,
    make_exchange,
)


ROOT = Path(__file__).parent
STATIC_DIR = ROOT / "static"
SETTINGS_PATH = ROOT / "settings.json"
LOG_DIR = ROOT / "logs"
APP_LOG_PATH = LOG_DIR / "app.log"
TRADE_LOG_PATH = LOG_DIR / "trades.jsonl"
SPREAD_LOG_PATH = LOG_DIR / "spread_history.jsonl"
FUTURES_SPREAD_LOG_PATH = LOG_DIR / "futures_spread_history.jsonl"
FUTURES_PAPER_DEMO_LOG_PATH = LOG_DIR / "futures_paper_demo.jsonl"
RELATIVE_FEATURE_LOG_PATH = LOG_DIR / "relative_features.jsonl"
RELATIVE_MARKET_DATA_LOG_PATH = LOG_DIR / "relative_market_data.jsonl"
HISTORICAL_CANDLE_LOG_PATH = LOG_DIR / "historical_candles.jsonl"
LIVE_CONFIRM_TEXT = "I_UNDERSTAND_REAL_ORDERS"

load_dotenv(ROOT / ".env")


class BotSettings(BaseModel):
    exchanges: str = Field(default="binance,okx,bitget")
    futures_exchanges: str = Field(default="binance,okx,hyperliquid")
    symbols: str = Field(default="BTC/USDT,ETH/USDT,SOL/USDT")
    trade_size_quote: float = Field(default=100, gt=0)
    optimize_trade_size: bool = Field(default=True)
    max_trade_size_quote: float = Field(default=1000, gt=0)
    min_net_profit_pct: float = Field(default=0.2, ge=0)
    default_taker_fee_pct: float = Field(default=0.1, ge=0)
    slippage_pct: float = Field(default=0.03, ge=0)
    poll_seconds: float = Field(default=5, ge=1)
    orderbook_limit: int = Field(default=10, ge=5, le=100)
    mode: str = Field(default="demo")
    auto_execute: bool = Field(default=False)
    live_confirm: str = Field(default="")


class ManualDemoTrade(BaseModel):
    symbol: str = Field(default="BTC/USDT")
    buy_exchange: str = Field(default="binance")
    sell_exchange: str = Field(default="okx")
    quote_amount: float = Field(default=100, gt=0)
    profit_quote: float = Field(default=0, ge=-1000000)


class DemoPriceAdjustment(BaseModel):
    exchange_id: str = Field(default="okx")
    symbol: str = Field(default="BTC/USDT")
    bid_adjust_pct: float = Field(default=0)
    ask_adjust_pct: float = Field(default=0)


class PreflightRequest(BaseModel):
    exchanges: str
    symbols: str
    quote_amount: float = Field(default=25, gt=0)


class FuturesPaperPosition(BaseModel):
    symbol: str
    direction: str
    entry_spread_pct: float
    quote_amount: float
    opened_at: str
    add_count: int = 0
    last_spread_pct: float


class RelativeTradeRequest(BaseModel):
    symbol: str = Field(default="")
    short_symbols: list[str] = Field(default_factory=list)
    mode: str = Field(default="manual")
    quote_amount: float = Field(default=10, gt=0)


class HistoricalCandlesRequest(BaseModel):
    symbols: str = Field(default="")
    exchanges: str = Field(default="")
    timeframe: str = Field(default="1m")
    days: int = Field(default=7, ge=1, le=730)
    start_date: str = Field(default="")
    end_date: str = Field(default="")
    limit_per_market: int = Field(default=1000, ge=50, le=1500)


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def decimal_str(value: Decimal) -> str:
    return format(value, "f")


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    return value


def settings_to_config(settings: BotSettings) -> Config:
    exchange_ids = [item.lower() for item in parse_csv(settings.exchanges)]
    symbols = [item.upper() for item in parse_csv(settings.symbols)]
    if len(exchange_ids) < 2:
        raise ValueError("蜿門ｼ墓園縺ｯ2縺､莉･荳頑欠螳壹＠縺ｦ縺上□縺輔＞")
    if not symbols:
        raise ValueError("驫俶氛縺ｯ1縺､莉･荳頑欠螳壹＠縺ｦ縺上□縺輔＞")
    if settings.mode not in {"demo", "live"}:
        raise ValueError("mode must be demo or live")

    return Config(
        exchanges=exchange_ids,
        symbols=symbols,
        trade_size_quote=Decimal(str(settings.trade_size_quote)),
        min_net_profit_pct=Decimal(str(settings.min_net_profit_pct)),
        default_taker_fee_pct=Decimal(str(settings.default_taker_fee_pct)),
        slippage_pct=Decimal(str(settings.slippage_pct)),
        poll_seconds=settings.poll_seconds,
        orderbook_limit=settings.orderbook_limit,
        mode=settings.mode,
        live_trading=os.getenv("LIVE_TRADING", "false").strip().lower() == "true",
        live_confirm=settings.live_confirm.strip(),
    )


def config_to_settings(config: Config) -> BotSettings:
    return BotSettings(
        exchanges=",".join(config.exchanges),
        futures_exchanges="binance,okx,hyperliquid",
        symbols=",".join(config.symbols),
        trade_size_quote=float(config.trade_size_quote),
        optimize_trade_size=True,
        max_trade_size_quote=float(os.getenv("MAX_OPTIMIZED_TRADE_QUOTE", "1000")),
        min_net_profit_pct=float(config.min_net_profit_pct),
        default_taker_fee_pct=float(config.default_taker_fee_pct),
        slippage_pct=float(config.slippage_pct),
        poll_seconds=config.poll_seconds,
        orderbook_limit=config.orderbook_limit,
        mode=config.mode,
    )


def load_saved_settings() -> BotSettings | None:
    if not SETTINGS_PATH.exists():
        return None
    try:
        return BotSettings.model_validate_json(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_settings(settings: BotSettings) -> None:
    data = settings.model_dump()
    data["live_confirm"] = ""
    SETTINGS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def append_text_log(path: Path, line: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(line.rstrip() + "\n")


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(to_jsonable(item), ensure_ascii=False, separators=(",", ":")) + "\n")


def read_tail_lines(path: Path, limit: int = 300) -> list[str]:
    if not path.exists():
        return []
    if limit <= 0:
        return []
    chunk_size = 1024 * 1024
    data = b""
    with path.open("rb") as file:
        file.seek(0, 2)
        position = file.tell()
        while position > 0 and data.count(b"\n") <= limit:
            read_size = min(chunk_size, position)
            position -= read_size
            file.seek(position)
            data = file.read(read_size) + data
    return data.decode("utf-8", errors="replace").splitlines()[-limit:]


def read_tail_jsonl(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    rows = []
    for line in read_tail_lines(path, limit):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"raw": line})
    return rows


def futures_event_report(
    entry_threshold: Decimal = Decimal("1.0"),
    add_threshold: Decimal = Decimal("1.5"),
    second_add_threshold: Decimal = Decimal("2.0"),
    exit_threshold: Decimal = Decimal("0.2"),
    cost_pct: Decimal = Decimal("0.24"),
    quote_amount: Decimal = Decimal("10"),
    limit: int = 200,
) -> list[dict[str, Any]]:
    if not FUTURES_SPREAD_LOG_PATH.exists():
        return []
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for line in FUTURES_SPREAD_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
            timestamp = datetime.fromisoformat(row["timestamp"])
        except Exception:
            continue
        for point in row.get("points", []):
            symbol = point.get("symbol")
            raw_spread = point.get("spread_pct")
            if not symbol or raw_spread is None:
                continue
            spread = Decimal(str(raw_spread))
            by_symbol.setdefault(symbol, []).append(
                {
                    "timestamp": timestamp,
                    "spread": spread,
                    "direction": point.get("direction", ""),
                    "net_spread": Decimal(str(point.get("net_spread_pct"))) if point.get("net_spread_pct") is not None else None,
                }
            )

    events: list[dict[str, Any]] = []
    for symbol, rows in by_symbol.items():
        rows.sort(key=lambda item: item["timestamp"])
        position: dict[str, Any] | None = None
        for item in rows:
            spread = item["spread"]
            if position is None:
                if spread >= entry_threshold:
                    position = {
                        "symbol": symbol,
                        "entry_time": item["timestamp"],
                        "entry_spread_pct": spread,
                        "direction": item["direction"],
                        "amount_no_add": quote_amount,
                        "amount_with_add": quote_amount,
                        "avg_with_add": spread,
                        "add_count": 0,
                        "max_spread_pct": spread,
                    }
                continue

            position["max_spread_pct"] = max(position["max_spread_pct"], spread)
            if position["add_count"] == 0 and spread >= add_threshold:
                amount = Decimal(str(position["amount_with_add"]))
                avg = Decimal(str(position["avg_with_add"]))
                position["avg_with_add"] = ((avg * amount) + (spread * quote_amount)) / (amount + quote_amount)
                position["amount_with_add"] = amount + quote_amount
                position["add_count"] = 1
            elif position["add_count"] == 1 and spread >= second_add_threshold:
                amount = Decimal(str(position["amount_with_add"]))
                avg = Decimal(str(position["avg_with_add"]))
                position["avg_with_add"] = ((avg * amount) + (spread * quote_amount)) / (amount + quote_amount)
                position["amount_with_add"] = amount + quote_amount
                position["add_count"] = 2

            if spread <= exit_threshold:
                entry = Decimal(str(position["entry_spread_pct"]))
                avg_add = Decimal(str(position["avg_with_add"]))
                amount_add = Decimal(str(position["amount_with_add"]))
                held_minutes = Decimal(str((item["timestamp"] - position["entry_time"]).total_seconds() / 60))
                events.append(
                    to_jsonable(
                        {
                            "symbol": symbol,
                            "entry_time": position["entry_time"],
                            "exit_time": item["timestamp"],
                            "held_minutes": held_minutes,
                            "direction": position["direction"],
                            "entry_spread_pct": entry,
                            "max_spread_pct": position["max_spread_pct"],
                            "exit_spread_pct": spread,
                            "add_count": position["add_count"],
                            "pnl_no_add": quote_amount * ((entry - spread - cost_pct) / Decimal("100")),
                            "pnl_with_add": amount_add * ((avg_add - spread - cost_pct) / Decimal("100")),
                            "amount_with_add": amount_add,
                            "cost_pct": cost_pct,
                        }
                    )
                )
                position = None
        if position is not None:
            latest = rows[-1]
            entry = Decimal(str(position["entry_spread_pct"]))
            avg_add = Decimal(str(position["avg_with_add"]))
            amount_add = Decimal(str(position["amount_with_add"]))
            held_minutes = Decimal(str((latest["timestamp"] - position["entry_time"]).total_seconds() / 60))
            events.append(
                to_jsonable(
                    {
                        "symbol": symbol,
                        "entry_time": position["entry_time"],
                        "exit_time": None,
                        "held_minutes": held_minutes,
                        "direction": position["direction"],
                        "entry_spread_pct": entry,
                        "max_spread_pct": position["max_spread_pct"],
                        "exit_spread_pct": latest["spread"],
                        "add_count": position["add_count"],
                        "pnl_no_add": quote_amount * ((entry - latest["spread"] - cost_pct) / Decimal("100")),
                        "pnl_with_add": amount_add * ((avg_add - latest["spread"] - cost_pct) / Decimal("100")),
                        "amount_with_add": amount_add,
                        "cost_pct": cost_pct,
                        "status": "open",
                    }
                )
            )
    events.sort(key=lambda item: item["entry_time"], reverse=True)
    return events[:limit]


def relative_learning_report(
    limit: int = 200,
    horizon_minutes: int = 30,
    assumed_cost_pct: Decimal = Decimal("0.10"),
    snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if snapshots is None:
        snapshots = read_tail_jsonl(RELATIVE_FEATURE_LOG_PATH, min(limit, 80))
    snapshots = [item for item in snapshots[-limit:] if item.get("features")]
    if len(snapshots) < 2:
        return {"status": "not_enough_data", "sample_count": len(snapshots), "trade_count": 0}

    def item_time(item: dict[str, Any]) -> datetime | None:
        try:
            return datetime.fromisoformat(str(item.get("timestamp")))
        except Exception:
            return None

    def row_price(row: dict[str, Any]) -> Decimal | None:
        for key in ("vwap", "mid", "ohlcv_close"):
            if row.get(key) is None:
                continue
            try:
                value = Decimal(str(row[key]))
                if value > 0:
                    return value
            except Exception:
                continue
        return None

    def rows_by_symbol(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {str(row.get("symbol")): row for row in item.get("features", []) if row.get("symbol")}

    time_rows = [(item_time(item), rows_by_symbol(item)) for item in snapshots]
    trades: list[dict[str, Any]] = []
    for index, entry in enumerate(snapshots[:-1]):
        entry_time = item_time(entry)
        if entry_time is None:
            continue
        exit_item = None
        for candidate in snapshots[index + 1 :]:
            candidate_time = item_time(candidate)
            if candidate_time and (candidate_time - entry_time).total_seconds() >= horizon_minutes * 60:
                exit_item = candidate
                break
        if exit_item is None:
            continue
        entry_rows = rows_by_symbol(entry)
        exit_rows = rows_by_symbol(exit_item)
        longs = [row for row in entry_rows.values() if row.get("long_candidate")]
        shorts = [row for row in entry_rows.values() if row.get("short_candidate")]
        if not longs or not shorts:
            continue
        long_row = max(longs, key=lambda row: Decimal(str(row.get("relative_score") or 0)))
        short_row = min(shorts, key=lambda row: Decimal(str(row.get("relative_score") or 0)))
        long_symbol = str(long_row.get("symbol"))
        short_symbol = str(short_row.get("symbol"))
        if long_symbol not in exit_rows or short_symbol not in exit_rows:
            continue
        long_entry = row_price(long_row)
        short_entry = row_price(short_row)
        long_exit = row_price(exit_rows[long_symbol])
        short_exit = row_price(exit_rows[short_symbol])
        if not long_entry or not short_entry or not long_exit or not short_exit:
            continue
        path = []
        max_favorable = Decimal("-999")
        max_adverse = Decimal("999")
        minutes_to_best: Decimal | None = None
        minutes_to_take_profit: Decimal | None = None
        minutes_to_stop_loss: Decimal | None = None
        for path_time, path_rows in time_rows[index + 1 :]:
            if path_time is None or path_time > item_time(exit_item):
                break
            if long_symbol not in path_rows or short_symbol not in path_rows:
                continue
            path_long = row_price(path_rows[long_symbol])
            path_short = row_price(path_rows[short_symbol])
            if not path_long or not path_short:
                continue
            held = Decimal(str((path_time - entry_time).total_seconds() / 60))
            long_path_return = ((path_long - long_entry) / long_entry) * Decimal("100")
            short_path_return = ((path_short - short_entry) / short_entry) * Decimal("100")
            path_relative = long_path_return - short_path_return
            path_net = path_relative - assumed_cost_pct
            path.append({"held_minutes": held, "net_pct": path_net})
            if path_net > max_favorable:
                max_favorable = path_net
                minutes_to_best = held
            if path_net < max_adverse:
                max_adverse = path_net
            if minutes_to_take_profit is None and path_net >= Decimal("0.30"):
                minutes_to_take_profit = held
            if minutes_to_stop_loss is None and path_net <= Decimal("-0.30"):
                minutes_to_stop_loss = held
        long_return = ((long_exit - long_entry) / long_entry) * Decimal("100")
        short_return = ((short_exit - short_entry) / short_entry) * Decimal("100")
        relative_return = long_return - short_return
        pnl_after_cost = relative_return - assumed_cost_pct
        trades.append(
            {
                "entry_time": entry_time,
                "exit_time": item_time(exit_item),
                "long_symbol": long_symbol,
                "short_symbol": short_symbol,
                "long_score": Decimal(str(long_row.get("relative_score") or 0)),
                "short_score": Decimal(str(short_row.get("relative_score") or 0)),
                "long_return_pct": long_return,
                "short_return_pct": short_return,
                "relative_return_pct": relative_return,
                "pnl_after_cost_pct": pnl_after_cost,
                "max_favorable_pct": max_favorable if max_favorable != Decimal("-999") else pnl_after_cost,
                "max_adverse_pct": max_adverse if max_adverse != Decimal("999") else pnl_after_cost,
                "minutes_to_best": minutes_to_best,
                "minutes_to_take_profit_030": minutes_to_take_profit,
                "minutes_to_stop_loss_030": minutes_to_stop_loss,
                "win": pnl_after_cost > 0,
                "long_parts": long_row.get("score_parts", {}),
                "short_parts": short_row.get("score_parts", {}),
            }
        )

    if not trades:
        return {"status": "not_enough_trades", "sample_count": len(snapshots), "trade_count": 0}

    wins = [trade for trade in trades if trade["win"]]
    losses = [trade for trade in trades if not trade["win"]]
    total = sum((Decimal(str(trade["pnl_after_cost_pct"])) for trade in trades), Decimal("0"))
    average = total / Decimal(str(len(trades)))

    def outcome_for_thresholds(trade: dict[str, Any], take_profit: Decimal, stop_loss: Decimal) -> dict[str, Any]:
        tp = trade.get("minutes_to_take_profit_030") if take_profit == Decimal("0.30") else None
        sl = trade.get("minutes_to_stop_loss_030") if stop_loss == Decimal("-0.30") else None
        if tp is not None and (sl is None or Decimal(str(tp)) <= Decimal(str(sl))):
            return {"status": "take_profit", "pnl_pct": take_profit, "held_minutes": tp}
        if sl is not None:
            return {"status": "stop_loss", "pnl_pct": stop_loss, "held_minutes": sl}
        return {"status": "time_exit", "pnl_pct": Decimal(str(trade["pnl_after_cost_pct"])), "held_minutes": horizon_minutes}

    threshold_results = []
    for take_profit in [Decimal("0.20"), Decimal("0.30"), Decimal("0.50"), Decimal("0.80"), Decimal("1.20")]:
        for stop_loss in [Decimal("-0.20"), Decimal("-0.30"), Decimal("-0.50"), Decimal("-0.80")]:
            outcomes = []
            for trade in trades:
                if take_profit == Decimal("0.30") and stop_loss == Decimal("-0.30"):
                    outcomes.append(outcome_for_thresholds(trade, take_profit, stop_loss))
                else:
                    # Use MFE/MAE approximation when exact first-touch time was not tracked for this threshold.
                    if Decimal(str(trade["max_adverse_pct"])) <= stop_loss:
                        outcomes.append({"status": "stop_loss", "pnl_pct": stop_loss, "held_minutes": None})
                    elif Decimal(str(trade["max_favorable_pct"])) >= take_profit:
                        outcomes.append({"status": "take_profit", "pnl_pct": take_profit, "held_minutes": None})
                    else:
                        outcomes.append({"status": "time_exit", "pnl_pct": Decimal(str(trade["pnl_after_cost_pct"])), "held_minutes": horizon_minutes})
            pnl_values = [Decimal(str(item["pnl_pct"])) for item in outcomes]
            threshold_results.append(
                {
                    "take_profit_pct": take_profit,
                    "stop_loss_pct": stop_loss,
                    "win_rate_pct": (Decimal(str(sum(1 for item in pnl_values if item > 0))) / Decimal(str(len(pnl_values)))) * Decimal("100"),
                    "avg_pnl_pct": sum(pnl_values) / Decimal(str(len(pnl_values))),
                    "take_profit_count": sum(1 for item in outcomes if item["status"] == "take_profit"),
                    "stop_loss_count": sum(1 for item in outcomes if item["status"] == "stop_loss"),
                    "time_exit_count": sum(1 for item in outcomes if item["status"] == "time_exit"),
                }
            )
    threshold_results.sort(key=lambda item: Decimal(str(item["avg_pnl_pct"])), reverse=True)

    def average_part(side: str, key: str, subset: list[dict[str, Any]]) -> Decimal:
        values = []
        part_key = f"{side}_parts"
        for trade in subset:
            parts = trade.get(part_key) or {}
            if parts.get(key) is None:
                continue
            try:
                values.append(Decimal(str(parts[key])))
            except Exception:
                continue
        return sum(values) / Decimal(str(len(values))) if values else Decimal("0")

    keys = ["1h_return_rank", "4h_return_rank", "1h_volume", "4h_volume", "bid_ask_depth_pressure", "ema20_position", "relative_rank", "rsi_overheat", "price_surge"]
    factor_summary = [
        {
            "key": key,
            "win_long_avg": average_part("long", key, wins),
            "loss_long_avg": average_part("long", key, losses),
            "win_short_avg": average_part("short", key, wins),
            "loss_short_avg": average_part("short", key, losses),
        }
        for key in keys
    ]
    return to_jsonable(
        {
            "status": "ok",
            "sample_count": len(snapshots),
            "trade_count": len(trades),
            "horizon_minutes": horizon_minutes,
            "assumed_cost_pct": assumed_cost_pct,
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate_pct": (Decimal(str(len(wins))) / Decimal(str(len(trades)))) * Decimal("100"),
            "avg_pnl_after_cost_pct": average,
            "avg_max_favorable_pct": sum((Decimal(str(trade["max_favorable_pct"])) for trade in trades), Decimal("0")) / Decimal(str(len(trades))),
            "avg_max_adverse_pct": sum((Decimal(str(trade["max_adverse_pct"])) for trade in trades), Decimal("0")) / Decimal(str(len(trades))),
            "best_exit_rules": threshold_results[:8],
            "best": max(trades, key=lambda trade: trade["pnl_after_cost_pct"]),
            "worst": min(trades, key=lambda trade: trade["pnl_after_cost_pct"]),
            "recent_trades": trades[-30:],
            "factor_summary": factor_summary,
        }
    )


def has_private_credentials(exchange_id: str) -> bool:
    prefix = exchange_id.upper()
    return bool(os.getenv(f"{prefix}_API_KEY") and os.getenv(f"{prefix}_SECRET"))


def run_preflight_sync(request: PreflightRequest, fallback_fee_pct: Decimal) -> list[dict[str, Any]]:
    exchange_ids = [item.lower() for item in parse_csv(request.exchanges)]
    symbols = [item.upper() for item in parse_csv(request.symbols)]
    quote_amount = Decimal(str(request.quote_amount))
    results: list[dict[str, Any]] = []

    for exchange_id in exchange_ids:
        exchange = make_sync_exchange(exchange_id, private=has_private_credentials(exchange_id))
        try:
            markets = exchange.load_markets()
            fees = (
                load_account_trading_fees_sync(exchange_id, symbols, fallback_fee_pct)
                if has_private_credentials(exchange_id)
                else {}
            )
            balance = {}
            if has_private_credentials(exchange_id):
                try:
                    fetched = exchange.fetch_balance()
                    balance = fetched.get("total") or {}
                except Exception:
                    balance = {}

            for symbol in symbols:
                market = markets.get(symbol)
                base, quote = symbol.split("/") if "/" in symbol else (symbol, "")
                if not market:
                    results.append(
                        {
                            "exchange_id": exchange_id,
                            "symbol": symbol,
                            "status": "ng",
                            "message": "取扱なし",
                        }
                    )
                    continue

                limits = market.get("limits") or {}
                amount_limits = limits.get("amount") or {}
                cost_limits = limits.get("cost") or {}
                precision = market.get("precision") or {}
                min_cost = cost_limits.get("min")
                min_amount = amount_limits.get("min")
                taker_fee_pct = fees.get(symbol, fallback_fee_pct)
                quote_balance = Decimal(str(balance.get(quote, 0) or 0)) if balance else None
                base_balance = Decimal(str(balance.get(base, 0) or 0)) if balance else None
                can_quote = quote_balance is None or quote_balance >= quote_amount
                message_parts = []
                if min_cost and quote_amount < Decimal(str(min_cost)):
                    message_parts.append(f"最小注文金額 {min_cost} を下回っています")
                if quote_balance is not None and not can_quote:
                    message_parts.append(f"{quote}谿矩ｫ倅ｸ崎ｶｳ")
                status = "ok" if not message_parts else "warn"
                results.append(
                    {
                        "exchange_id": exchange_id,
                        "symbol": symbol,
                        "status": status,
                        "message": " / ".join(message_parts) or "蜿門ｼ募庄閭ｽ",
                        "taker_fee_pct": taker_fee_pct,
                        "fee_source": "account" if symbol in fees else "fallback",
                        "min_cost": min_cost,
                        "min_amount": min_amount,
                        "amount_precision": precision.get("amount"),
                        "price_precision": precision.get("price"),
                        "quote_balance": quote_balance,
                        "base_balance": base_balance,
                    }
                )
        except Exception as exc:
            for symbol in symbols:
                results.append(
                    {
                        "exchange_id": exchange_id,
                        "symbol": symbol,
                        "status": "ng",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )
        finally:
            try:
                exchange.close()
            except Exception:
                pass

    return to_jsonable(results)


class DemoBroker:
    def __init__(self) -> None:
        self.cash = Decimal("10000")
        self.realized_profit = Decimal("0")
        self.trades: deque[dict[str, Any]] = deque(maxlen=200)
        self.seen: set[str] = set()

    def reset(self, cash: Decimal = Decimal("10000")) -> None:
        self.cash = cash
        self.realized_profit = Decimal("0")
        self.trades.clear()
        self.seen.clear()

    def execute(self, opportunity: Opportunity, mode: str) -> dict[str, Any] | None:
        signature = (
            f"{opportunity.symbol}:{opportunity.buy_exchange}:{opportunity.sell_exchange}:"
            f"{opportunity.buy_price}:{opportunity.sell_price}"
        )
        if signature in self.seen:
            return None
        self.seen.add(signature)

        profit = opportunity.estimated_profit_quote
        self.cash += profit
        self.realized_profit += profit
        trade = {
            "timestamp": datetime.now(timezone.utc),
            "symbol": opportunity.symbol,
            "buy_exchange": opportunity.buy_exchange,
            "sell_exchange": opportunity.sell_exchange,
            "buy_price": opportunity.buy_price,
            "sell_price": opportunity.sell_price,
            "base_amount": opportunity.base_amount,
            "quote_amount": opportunity.quote_amount,
            "net_profit_pct": opportunity.net_profit_pct,
            "profit_quote": profit,
            "mode": mode,
            "status": "simulated_fill",
        }
        self.trades.appendleft(to_jsonable(trade))
        append_jsonl(TRADE_LOG_PATH, trade)
        return trade

    def portfolio(self) -> dict[str, Any]:
        return to_jsonable(
            {
                "cash": self.cash,
                "realized_profit": self.realized_profit,
                "trade_count": len(self.trades),
            }
        )

    def manual_trade(self, request: ManualDemoTrade) -> dict[str, Any]:
        quote_amount = Decimal(str(request.quote_amount))
        profit = Decimal(str(request.profit_quote))
        net_profit_pct = (profit / quote_amount) * Decimal("100")
        self.cash += profit
        self.realized_profit += profit
        trade = {
            "timestamp": datetime.now(timezone.utc),
            "symbol": request.symbol.strip().upper(),
            "buy_exchange": request.buy_exchange.strip().lower(),
            "sell_exchange": request.sell_exchange.strip().lower(),
            "buy_price": Decimal("0"),
            "sell_price": Decimal("0"),
            "base_amount": Decimal("0"),
            "quote_amount": quote_amount,
            "net_profit_pct": net_profit_pct,
            "profit_quote": profit,
            "mode": "demo",
            "status": "manual_fill",
        }
        self.trades.appendleft(to_jsonable(trade))
        append_jsonl(TRADE_LOG_PATH, trade)
        return trade


class BotRuntime:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.stop_event: asyncio.Event | None = None
        self.lock = asyncio.Lock()
        self.logs: deque[dict[str, str]] = deque(maxlen=500)
        self.quotes: list[dict[str, Any]] = []
        self.market_statuses: list[dict[str, Any]] = []
        self.opportunities: list[dict[str, Any]] = []
        self.spread_history: deque[dict[str, Any]] = deque(maxlen=300)
        self.futures_spread_history: deque[dict[str, Any]] = deque(maxlen=500)
        self.futures_market_statuses: list[dict[str, Any]] = []
        self.futures_perf: dict[str, Any] = {}
        self.futures_base_symbols: list[str] = []
        self.futures_active_symbols: list[str] = []
        self.futures_boost_symbols: dict[str, datetime] = {}
        self.futures_movement_symbols: dict[str, Decimal] = {}
        self.balances: list[dict[str, Any]] = []
        self.exchange_handles: dict[str, Any] = {}
        self.preflight_results: list[dict[str, Any]] = []
        self.demo_price_adjustments: dict[tuple[str, str], dict[str, Decimal]] = {}
        self.futures_positions: dict[str, dict[str, Any]] = {}
        self.futures_closed_trades: deque[dict[str, Any]] = deque(maxlen=300)
        self.futures_unrealized_profit = Decimal("0")
        self.futures_realized_profit = Decimal("0")
        self.relative_history: deque[dict[str, Any]] = deque(maxlen=12000)
        self.relative_feature_history: deque[dict[str, Any]] = deque(maxlen=12000)
        self.relative_market_history: deque[dict[str, Any]] = deque(maxlen=12000)
        self.last_relative_market_data_at: datetime | None = None
        self.relative_rankings: dict[str, Any] = {}
        self.relative_positions: dict[str, dict[str, Any]] = {}
        self.relative_closed_trades: deque[dict[str, Any]] = deque(maxlen=200)
        self.relative_realized_profit = Decimal("0")
        self.relative_unrealized_profit = Decimal("0")
        self.historical_candle_status: dict[str, Any] = {}
        self._historical_candle_cache_mtime: float | None = None
        self._historical_candle_cache: dict[str, dict[str, Any]] = {}
        self.demo = DemoBroker()
        self.settings = load_saved_settings() or config_to_settings(load_config())
        self.last_error: str | None = None
        self.last_tick: str | None = None
        self.stopped_at: str | None = None

    def log(self, level: str, message: str) -> None:
        now = datetime.now().astimezone()
        item = {
            "time": now.strftime("%H:%M:%S"),
            "timestamp": now.isoformat(),
            "level": level,
            "message": message,
        }
        self.logs.appendleft(item)
        append_text_log(APP_LOG_PATH, f"{item['timestamp']}\t{level}\t{message}")

    async def start(self, settings: BotSettings) -> None:
        async with self.lock:
            if self.task and not self.task.done():
                self.log("info", "Already running")
                return
            config = settings_to_config(settings)
            if config.mode == "live":
                self._validate_live_config(config)

            self.settings = settings
            save_settings(settings)
            self.market_statuses = [
                {
                    "exchange_id": exchange_id,
                    "symbol": symbol,
                    "status": "pending",
                    "message": "Starting",
                }
                for exchange_id in config.exchanges
                for symbol in config.symbols
            ]
            self.quotes = []
            self.opportunities = []
            self.last_error = None
            self.stopped_at = None
            self.stop_event = asyncio.Event()
            self.task = asyncio.create_task(self._run(config, settings.auto_execute))
            self.log("info", f"{config.mode.upper()} 繝｢繝ｼ繝峨〒繧ｹ繧ｭ繝｣繝翫・繧帝幕蟋九＠縺ｾ縺励◆")

    async def stop(self) -> None:
        async with self.lock:
            if self.stop_event:
                self.stop_event.set()
                self.log("info", "蛛懈ｭ｢繝ｪ繧ｯ繧ｨ繧ｹ繝医ｒ騾√ｊ縺ｾ縺励◆")

    def _validate_live_config(self, config: Config) -> None:
        if not config.live_trading:
            raise HTTPException(status_code=400, detail="LIVE_TRADING=true is required")
        if config.live_confirm != LIVE_CONFIRM_TEXT:
            raise HTTPException(status_code=400, detail=f"譛ｬ逡ｪ遒ｺ隱肴ｬ・↓ {LIVE_CONFIRM_TEXT} 繧貞・蜉帙＠縺ｦ縺上□縺輔＞")
        missing = []
        for exchange_id in config.exchanges:
            prefix = exchange_id.upper()
            if not os.getenv(f"{prefix}_API_KEY") or not os.getenv(f"{prefix}_SECRET"):
                missing.append(exchange_id)
        if missing:
            raise HTTPException(status_code=400, detail=f"API繧ｭ繝ｼ譛ｪ險ｭ螳・ {', '.join(missing)}")

    async def _run(self, config: Config, auto_execute: bool) -> None:
        futures_exchanges = []
        try:
            futures_exchanges = await self._prepare_futures_exchanges(config)
            if len(futures_exchanges) < 2:
                raise RuntimeError("Need at least two futures research exchanges")
            if self._should_use_common_futures(config.symbols):
                common_symbols = await self._discover_common_futures_symbols(futures_exchanges)
                if not common_symbols:
                    raise RuntimeError("No common futures symbols found")
                config = replace(config, symbols=common_symbols)
                self.log("ready", f"Common futures symbols: {len(common_symbols)}")
            filtered_symbols = await self._filter_symbols_with_futures_books(
                futures_exchanges,
                config.symbols,
                config.orderbook_limit,
            )
            if not filtered_symbols:
                raise RuntimeError("No futures symbols with live order books found")
            if filtered_symbols != config.symbols:
                config = replace(config, symbols=filtered_symbols)
                self.log("ready", f"Live-book futures symbols: {len(filtered_symbols)}")
            hot_poll_seconds = Decimal(os.getenv("FUTURES_HOT_POLL_SECONDS", "3"))
            if Decimal(str(config.poll_seconds)) > hot_poll_seconds:
                config = replace(config, poll_seconds=int(hot_poll_seconds))
                self.log("ready", f"Hot futures poll interval: {config.poll_seconds}s")
            self.futures_base_symbols = list(config.symbols)
            self.futures_active_symbols = list(config.symbols)
            self.log("ready", f"FUTURES research {', '.join(config.symbols)}: {', '.join(futures_exchanges)}")

            while self.stop_event and not self.stop_event.is_set():
                active_symbols = self._select_hot_futures_symbols(config.symbols)
                self.futures_active_symbols = active_symbols
                wait_seconds = self._adaptive_futures_poll_seconds(config)
                scan_config = replace(config, symbols=active_symbols, poll_seconds=wait_seconds)
                await self._record_futures_spread_history(futures_exchanges, scan_config)
                latest = self.futures_spread_history[-1] if self.futures_spread_history else {"points": []}
                self.quotes = []
                self.market_statuses = self.futures_market_statuses
                self.opportunities = []
                self.last_tick = datetime.now().astimezone().isoformat()

                if latest.get("points"):
                    self._update_futures_boost_symbols(latest["points"])
                    self._update_futures_movement_boost_symbols(config.symbols)
                    best = latest["points"][0]
                    self.log(
                        "futures",
                        f"{best['symbol']} {best['direction']} gross {Decimal(str(best['spread_pct'])):.4f}% net {Decimal(str(best.get('net_spread_pct') or 0)):.4f}%",
                    )
                    if auto_execute:
                        self._update_futures_paper_strategy(latest["points"])
                        self._update_relative_auto_strategy()
                else:
                    self.log("futures", "No futures spread data")

                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=wait_seconds)
                except asyncio.TimeoutError:
                    pass
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.log("error", self.last_error)
        finally:
            self.stopped_at = datetime.now().astimezone().isoformat()
            self.log("info", "Futures research stopped")

    def _adaptive_futures_poll_seconds(self, config: Config) -> float:
        now = datetime.now(timezone.utc)
        self.futures_boost_symbols = {
            symbol: expires_at for symbol, expires_at in self.futures_boost_symbols.items() if expires_at > now
        }
        if self.futures_boost_symbols or self.futures_positions:
            try:
                return max(1.0, float(os.getenv("FUTURES_BOOST_POLL_SECONDS", "1")))
            except ValueError:
                return 1.0
        return float(config.poll_seconds)

    def _update_futures_boost_symbols(self, points: list[dict[str, Any]]) -> None:
        net_threshold = Decimal(os.getenv("FUTURES_BOOST_NET_SPREAD_PCT", "0.25"))
        gross_threshold = Decimal(os.getenv("FUTURES_BOOST_GROSS_SPREAD_PCT", "0.6"))
        ttl_seconds = int(os.getenv("FUTURES_BOOST_TTL_SECONDS", "180"))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        for point in points:
            symbol = str(point.get("symbol", ""))
            if not symbol:
                continue
            net = point.get("net_spread_pct")
            gross = Decimal(str(point.get("spread_pct") or 0))
            net_value = Decimal(str(net)) if net is not None else Decimal("-999")
            if net_value >= net_threshold or gross >= gross_threshold:
                self.futures_boost_symbols[symbol] = expires_at

    def _update_futures_movement_boost_symbols(self, symbols: list[str]) -> None:
        if len(self.relative_history) < 2:
            return
        one_minute_threshold = Decimal(os.getenv("FUTURES_MOVEMENT_1M_PCT", "1.0"))
        five_minute_threshold = Decimal(os.getenv("FUTURES_MOVEMENT_5M_PCT", "2.0"))
        ttl_seconds = int(os.getenv("FUTURES_BOOST_TTL_SECONDS", "180"))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        movement_scores: dict[str, Decimal] = {}
        rows_1m = self._relative_returns(1)
        rows_5m = self._relative_returns(5)
        for row in rows_1m:
            symbol = str(row.get("symbol", "")).upper()
            if symbol in symbols:
                movement_scores[symbol] = max(movement_scores.get(symbol, Decimal("0")), abs(Decimal(str(row.get("return_pct") or 0))))
        for row in rows_5m:
            symbol = str(row.get("symbol", "")).upper()
            if symbol in symbols:
                movement_scores[symbol] = max(
                    movement_scores.get(symbol, Decimal("0")),
                    abs(Decimal(str(row.get("return_pct") or 0))) * Decimal("0.6"),
                )
        self.futures_movement_symbols = dict(sorted(movement_scores.items(), key=lambda item: item[1], reverse=True)[:12])
        for symbol, score in movement_scores.items():
            one_minute = next((abs(Decimal(str(row.get("return_pct") or 0))) for row in rows_1m if row.get("symbol") == symbol), Decimal("0"))
            five_minute = next((abs(Decimal(str(row.get("return_pct") or 0))) for row in rows_5m if row.get("symbol") == symbol), Decimal("0"))
            if one_minute >= one_minute_threshold or five_minute >= five_minute_threshold:
                self.futures_boost_symbols[symbol] = expires_at

    def _select_hot_futures_symbols(self, symbols: list[str]) -> list[str]:
        if not symbols:
            return []
        try:
            limit = int(os.getenv("FUTURES_HOT_SYMBOL_LIMIT", "24"))
        except ValueError:
            limit = 24
        limit = max(5, min(limit, len(symbols)))

        # First scans stay broad so every shared futures symbol gets a baseline.
        if len(self.relative_history) < 6 or len(symbols) <= limit:
            return list(symbols)

        forced = {position.get("symbol") for position in self.futures_positions.values() if position.get("symbol")}
        forced.update(self.futures_boost_symbols.keys())
        forced.update(self.futures_movement_symbols.keys())
        for position in self.relative_positions.values():
            if position.get("long_symbol"):
                forced.add(position["long_symbol"])
            for symbol in position.get("short_symbols", []):
                forced.add(symbol)
        scores: dict[str, Decimal] = {}
        rows = (self.relative_rankings.get("strong") or []) + (self.relative_rankings.get("weak") or [])
        for row in rows:
            symbol = str(row.get("symbol", "")).upper()
            if symbol not in symbols:
                continue
            one_hour = abs(Decimal(str(row.get("return_1h_pct") or 0)))
            four_hour = abs(Decimal(str(row.get("return_4h_pct") or 0)))
            scores[symbol] = max(scores.get(symbol, Decimal("0")), one_hour + (four_hour * Decimal("0.35")))

        if not scores:
            return list(symbols[:limit])

        ranked = sorted(symbols, key=lambda symbol: (symbol in forced, scores.get(symbol, Decimal("0"))), reverse=True)
        active: list[str] = []
        for symbol in list(forced) + ranked:
            if symbol in symbols and symbol not in active:
                active.append(symbol)
            if len(active) >= max(limit, len(forced)):
                break
        return active

    async def _prepare_exchanges(self, config: Config):
        exchanges = [make_exchange(exchange_id, private=config.mode == "live") for exchange_id in config.exchanges]
        ready = []
        try:
            for exchange in exchanges:
                try:
                    use_account_fee = has_private_credentials(exchange.id)
                    fee_source = "account" if use_account_fee else "market"
                    fees = (
                        await asyncio.to_thread(
                            load_account_trading_fees_sync,
                            exchange.id,
                            config.symbols,
                            config.default_taker_fee_pct,
                        )
                        if use_account_fee
                        else await load_markets_and_fees(exchange, config.symbols, config.default_taker_fee_pct)
                    )
                    if not fees:
                        fees = {symbol: config.default_taker_fee_pct for symbol in config.symbols}
                        fee_source = "fallback"
                    exchange._arb_taker_fee_pct_by_symbol = fees
                    exchange._arb_fee_source_by_symbol = {symbol: fee_source for symbol in fees}
                    ready.append(exchange)
                    for symbol, fee in fees.items():
                        self._upsert_market_status(
                            {
                                "exchange_id": exchange.id,
                                "symbol": symbol,
                                "status": "ready",
                                "message": "蟶ょｴ諠・ｱ蜿門ｾ玲ｸ医∩",
                                "taker_fee_pct": fee,
                                "fee_source": fee_source,
                            }
                        )
                    self.log("ready", f"{exchange.id}: {len(fees)} symbols")
                except Exception as exc:
                    exchange._arb_taker_fee_pct_by_symbol = {
                        symbol: config.default_taker_fee_pct for symbol in config.symbols
                    }
                    exchange._arb_fee_source_by_symbol = {
                        symbol: "fallback" for symbol in config.symbols
                    }
                    ready.append(exchange)
                    self.log("warn", f"{exchange.id}: 蟶ょｴ諠・ｱ縺ｯ螟ｱ謨励∫峩謗･譚ｿ蜿門ｾ励∈繝輔か繝ｼ繝ｫ繝舌ャ繧ｯ縺励∪縺・ {type(exc).__name__}: {exc}")
                    for symbol in config.symbols:
                        self._upsert_market_status(
                            {
                                "exchange_id": exchange.id,
                                "symbol": symbol,
                                "status": "ready",
                                "message": "Using fallback fee; price uses direct API",
                                "taker_fee_pct": config.default_taker_fee_pct,
                                "fee_source": "fallback",
                            }
                        )

            if len(ready) < 2:
                raise RuntimeError("Need at least two exchanges with configured symbols")
            self.exchange_handles = {exchange.id: exchange for exchange in ready}
            return ready
        except Exception:
            await asyncio.gather(*(exchange.close() for exchange in ready), return_exceptions=True)
            raise

    async def _prepare_futures_exchanges(self, config: Config):
        requested = [item.lower() for item in parse_csv(self.settings.futures_exchanges)]
        ready = [exchange_id for exchange_id in requested if exchange_id in {"binance", "okx", "bitget", "hyperliquid"}]
        if len(ready) >= 2:
            self.log("ready", f"futures direct API: {', '.join(ready)}")
        else:
            self.log("warn", "futures direct API needs at least two supported exchanges")
        return ready

    def _should_use_common_futures(self, symbols: list[str]) -> bool:
        markers = {"ALL", "COMMON", "COMMON_FUTURES", "AUTO"}
        return any(symbol.upper().replace("/", "_") in markers for symbol in symbols)

    async def _discover_common_futures_symbols(self, exchange_ids: list[str]) -> list[str]:
        symbol_sets = await asyncio.gather(
            *[self._fetch_futures_symbol_set(exchange_id) for exchange_id in exchange_ids],
            return_exceptions=True,
        )
        clean_sets = [item for item in symbol_sets if isinstance(item, set) and item]
        if len(clean_sets) < 2:
            return []
        common = set.intersection(*clean_sets)
        priority = [
            "BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "AVAX", "LINK", "TRX",
            "DOT", "LTC", "BCH", "UNI", "NEAR", "APT", "ARB", "OP", "SUI", "FIL",
            "ATOM", "INJ", "ETC", "HBAR", "ICP", "FET", "WIF", "PEPE", "SHIB",
        ]
        ordered_bases = [base for base in priority if base in common]
        ordered_bases.extend(sorted(base for base in common if base not in set(priority)))
        return [f"{base}/USDT" for base in ordered_bases[:80]]

    async def _fetch_futures_symbol_set(self, exchange_id: str) -> set[str]:
        headers = {"User-Agent": "arb-bot/1.0"}
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            if exchange_id == "binance":
                async with session.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=10) as response:
                    response.raise_for_status()
                    data = await response.json()
                return {
                    item["baseAsset"].upper()
                    for item in data.get("symbols", [])
                    if item.get("quoteAsset") == "USDT"
                    and item.get("contractType") == "PERPETUAL"
                    and item.get("status") == "TRADING"
                }
            if exchange_id == "hyperliquid":
                async with session.post("https://api.hyperliquid.xyz/info", json={"type": "meta"}, timeout=10) as response:
                    response.raise_for_status()
                    data = await response.json()
                return {item.get("name", "").upper() for item in data.get("universe", []) if item.get("name")}
            if exchange_id == "okx":
                async with session.get("https://www.okx.com/api/v5/public/instruments?instType=SWAP", timeout=10) as response:
                    response.raise_for_status()
                    data = await response.json()
                symbols = set()
                for item in data.get("data", []):
                    inst_id = item.get("instId", "")
                    if item.get("settleCcy") == "USDT" and item.get("state") == "live" and inst_id.endswith("-USDT-SWAP"):
                        symbols.add(inst_id.split("-")[0].upper())
                return symbols
            if exchange_id == "bitget":
                async with session.get("https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES", timeout=10) as response:
                    response.raise_for_status()
                    data = await response.json()
                return {
                    item.get("baseCoin", "").upper()
                    for item in data.get("data", [])
                    if item.get("quoteCoin") == "USDT" and item.get("symbolStatus") == "normal"
                }
        return set()

    async def _filter_symbols_with_futures_books(self, exchange_ids: list[str], symbols: list[str], limit: int) -> list[str]:
        checks = await asyncio.gather(
            *[
                self._fetch_futures_quote(exchange_id, symbol, Config(
                    exchanges=[],
                    symbols=[],
                    trade_size_quote=Decimal("1"),
                    min_net_profit_pct=Decimal("0"),
                    default_taker_fee_pct=Decimal("0"),
                    slippage_pct=Decimal("0"),
                    poll_seconds=1,
                    orderbook_limit=limit,
                    mode="demo",
                    live_trading=False,
                    live_confirm="",
                ))
                for exchange_id in exchange_ids
                for symbol in symbols
            ],
            return_exceptions=True,
        )
        status_by_symbol: dict[str, set[str]] = {symbol: set() for symbol in symbols}
        for item in checks:
            if isinstance(item, dict) and item.get("status") == "ok":
                status_by_symbol.setdefault(item["symbol"], set()).add(item["exchange_id"])
        min_exchanges = int(os.getenv("FUTURES_MIN_EXCHANGES_PER_SYMBOL", "2"))
        min_exchanges = max(2, min(min_exchanges, len(exchange_ids)))
        filtered = [symbol for symbol in symbols if len(status_by_symbol.get(symbol, set())) >= min_exchanges]
        removed = len(symbols) - len(filtered)
        if removed:
            self.log("ready", f"Filtered {removed} symbols with fewer than {min_exchanges} live futures exchanges")
        return filtered

    def _find_usdt_swap_symbol(self, markets: dict[str, Any], spot_symbol: str) -> str | None:
        base, quote = spot_symbol.split("/") if "/" in spot_symbol else (spot_symbol, "USDT")
        candidates = [f"{base}/USDT:USDT", spot_symbol]
        for candidate in candidates:
            market = markets.get(candidate)
            if market and market.get("swap") and market.get("linear"):
                return candidate
        for symbol, market in markets.items():
            if (
                market.get("base") == base
                and market.get("quote") == "USDT"
                and market.get("settle") == "USDT"
                and market.get("swap")
                and market.get("linear")
            ):
                return symbol
        return None

    def _scan_sizes(self, settings: BotSettings, config: Config) -> list[Decimal]:
        if not settings.optimize_trade_size:
            return [config.trade_size_quote]

        max_size = min(Decimal(str(settings.max_trade_size_quote)), self.demo.cash)
        raw_sizes = [
            Decimal("25"),
            Decimal("50"),
            Decimal("100"),
            Decimal("250"),
            Decimal("500"),
            Decimal("1000"),
            Decimal("2500"),
            Decimal("5000"),
        ]
        sizes = [size for size in raw_sizes if size <= max_size]
        if max_size not in sizes:
            sizes.append(max_size)
        return sorted({size for size in sizes if size > 0})

    def _record_spread_history(self, quotes: list[Quote], config: Config) -> None:
        by_symbol: dict[str, list[Quote]] = {}
        for quote in quotes:
            by_symbol.setdefault(quote.symbol, []).append(quote)

        points = []
        for symbol, items in by_symbol.items():
            if len(items) < 2:
                continue
            buy = min(items, key=lambda item: item.ask)
            sell = max(items, key=lambda item: item.bid)
            if buy.exchange_id == sell.exchange_id or buy.ask <= 0:
                continue
            gross_pct = ((sell.bid - buy.ask) / buy.ask) * Decimal("100")
            cost_pct = buy.taker_fee_pct + sell.taker_fee_pct + (config.slippage_pct * Decimal("2"))
            net_pct = gross_pct - cost_pct
            points.append(
                {
                    "symbol": symbol,
                    "buy_exchange": buy.exchange_id,
                    "sell_exchange": sell.exchange_id,
                    "gross_pct": gross_pct,
                    "net_pct": net_pct,
                }
            )

        item = to_jsonable(
            {
                "timestamp": datetime.now(timezone.utc),
                "points": points,
            }
        )
        self.spread_history.append(item)
        append_jsonl(SPREAD_LOG_PATH, item)

    async def _record_futures_spread_history(self, exchanges, config: Config) -> None:
        started = datetime.now(timezone.utc)
        quote_results = await asyncio.gather(
            *[
                self._fetch_futures_quote(exchange_id, symbol, config)
                for exchange_id in exchanges
                for symbol in config.symbols
            ],
            return_exceptions=True,
        )
        quotes = [item for item in quote_results if isinstance(item, dict) and item.get("status") == "ok"]
        statuses = [item for item in quote_results if isinstance(item, dict)]
        self.futures_market_statuses = to_jsonable(statuses)
        status_counts: dict[str, int] = {}
        for status in statuses:
            key = str(status.get("status", "unknown"))
            status_counts[key] = status_counts.get(key, 0) + 1

        by_symbol: dict[str, list[dict[str, Any]]] = {}
        for quote in quotes:
            by_symbol.setdefault(quote["symbol"], []).append(quote)

        points = []
        quote_amount = Decimal(os.getenv("FUTURES_PAPER_QUOTE", "10"))
        for symbol, items in by_symbol.items():
            if len(items) < 2:
                continue
            pairs = [
                (low, high)
                for low in items
                for high in items
                if low["exchange_id"] != high["exchange_id"] and low["mid"] > 0
            ]
            if not pairs:
                continue
            low, high = max(pairs, key=lambda pair: pair[1]["mid"] - pair[0]["mid"])
            spread_pct = ((high["mid"] - low["mid"]) / low["mid"]) * Decimal("100")
            long_entry = self._weighted_futures_price(low["asks"], quote_amount)
            short_entry = self._weighted_futures_price(high["bids"], quote_amount)
            executable = long_entry is not None and short_entry is not None
            executable_spread_pct = None
            net_spread_pct = None
            cost_pct = self._futures_round_trip_cost_pct(low["exchange_id"], high["exchange_id"], config)
            capacity_quote = min(Decimal(str(low.get("ask_capacity_quote", "0"))), Decimal(str(high.get("bid_capacity_quote", "0"))))
            if executable:
                long_price, _ = long_entry
                short_price, _ = short_entry
                executable_spread_pct = ((short_price - long_price) / long_price) * Decimal("100")
                net_spread_pct = executable_spread_pct - cost_pct
            points.append(
                {
                    "symbol": symbol,
                    "long_exchange": low["exchange_id"],
                    "short_exchange": high["exchange_id"],
                    "low_mid": low["mid"],
                    "high_mid": high["mid"],
                    "spread_pct": spread_pct,
                    "executable_spread_pct": executable_spread_pct,
                    "net_spread_pct": net_spread_pct,
                    "round_trip_cost_pct": cost_pct,
                    "capacity_quote": capacity_quote,
                    "is_executable": executable,
                    "direction": f"long {low['exchange_id']} / short {high['exchange_id']}",
                }
            )

        item = to_jsonable(
            {
                "timestamp": datetime.now(timezone.utc),
                "points": sorted(
                    points,
                    key=lambda point: Decimal(str(point.get("net_spread_pct") or point["spread_pct"])),
                    reverse=True,
                ),
            }
        )
        self.futures_spread_history.append(item)
        append_jsonl(FUTURES_SPREAD_LOG_PATH, item)
        self._record_relative_snapshot(quotes)
        await self._record_relative_market_data(exchanges, config.symbols, quotes)
        self._refresh_relative_pnl()
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        self.futures_perf = {
            "last_scan_seconds": Decimal(str(round(elapsed, 3))),
            "poll_seconds": Decimal(str(config.poll_seconds)),
            "symbol_count": len(config.symbols),
            "base_symbol_count": len(self.futures_base_symbols),
            "active_symbol_count": len(self.futures_active_symbols or config.symbols),
            "exchange_count": len(exchanges),
            "request_count": len(config.symbols) * len(exchanges),
            "ok_count": status_counts.get("ok", 0),
            "no_quote_count": status_counts.get("no_quote", 0),
            "error_count": status_counts.get("error", 0),
            "point_count": len(points),
            "load_pct": Decimal(str(round((elapsed / config.poll_seconds) * 100, 1))) if config.poll_seconds else Decimal("0"),
            "status_counts": status_counts,
            "updated_at": datetime.now(timezone.utc),
        }

    async def _fetch_futures_quote(self, exchange_id: str, spot_symbol: str, config: Config) -> dict[str, Any]:
        try:
            orderbook = await self._fetch_direct_futures_orderbook(exchange_id, spot_symbol, config.orderbook_limit)
            bids = orderbook.get("bids") or []
            asks = orderbook.get("asks") or []
            if not bids or not asks:
                return {
                    "exchange_id": exchange_id,
                    "symbol": spot_symbol,
                    "futures_symbol": self._futures_symbol(exchange_id, spot_symbol),
                    "status": "no_quote",
                    "message": "futures order book is empty",
                }
            bid = Decimal(str(bids[0][0]))
            ask = Decimal(str(asks[0][0]))
            return {
                "exchange_id": exchange_id,
                "symbol": spot_symbol,
                "futures_symbol": self._futures_symbol(exchange_id, spot_symbol),
                "status": "ok",
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / Decimal("2"),
                "bids": bids,
                "asks": asks,
                "bid_capacity_quote": self._book_capacity_quote(bids),
                "ask_capacity_quote": self._book_capacity_quote(asks),
                "timestamp": datetime.now(timezone.utc),
            }
        except Exception as exc:
            return {
                "exchange_id": exchange_id,
                "symbol": spot_symbol,
                "futures_symbol": self._futures_symbol(exchange_id, spot_symbol),
                "status": "error",
                "message": f"{type(exc).__name__}: {exc}",
            }

    def _futures_symbol(self, exchange_id: str, symbol: str) -> str:
        compact = symbol.replace("/", "").replace("-", "").upper()
        if exchange_id == "okx":
            return symbol.replace("/", "-").upper() + "-SWAP"
        if exchange_id == "hyperliquid":
            return symbol.split("/")[0].upper()
        return compact

    async def _fetch_direct_futures_orderbook(self, exchange_id: str, symbol: str, limit: int) -> dict[str, Any]:
        compact = symbol.replace("/", "").replace("-", "").upper()
        headers = {"User-Agent": "arb-bot/1.0"}
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())

        if exchange_id == "binance":
            url = f"https://fapi.binance.com/fapi/v1/depth?symbol={compact}&limit={limit}"
        elif exchange_id == "okx":
            inst_id = symbol.replace("/", "-").upper() + "-SWAP"
            url = f"https://www.okx.com/api/v5/market/books?instId={inst_id}&sz={limit}"
        elif exchange_id == "bitget":
            url = f"https://api.bitget.com/api/v2/mix/market/orderbook?symbol={compact}&productType=USDT-FUTURES&limit={limit}"
        elif exchange_id == "hyperliquid":
            url = "https://api.hyperliquid.xyz/info"
        else:
            raise ValueError(f"unsupported futures exchange: {exchange_id}")

        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            if exchange_id == "hyperliquid":
                coin = symbol.split("/")[0].upper()
                async with session.post(url, json={"type": "l2Book", "coin": coin}, timeout=10) as response:
                    response.raise_for_status()
                    data = await response.json()
            else:
                async with session.get(url, timeout=10) as response:
                    response.raise_for_status()
                    data = await response.json()

        if exchange_id == "okx":
            book = (data.get("data") or [{}])[0]
            return {"bids": book.get("bids", []), "asks": book.get("asks", [])}
        if exchange_id == "bitget":
            book = data.get("data") or {}
            return {"bids": book.get("bids", []), "asks": book.get("asks", [])}
        if exchange_id == "hyperliquid":
            levels = data.get("levels") or [[], []]
            bids = [[level.get("px"), level.get("sz")] for level in levels[0][:limit]]
            asks = [[level.get("px"), level.get("sz")] for level in levels[1][:limit]]
            return {"bids": bids, "asks": asks}
        return {"bids": data.get("bids", []), "asks": data.get("asks", [])}

    def _timeframe_ms(self, timeframe: str) -> int:
        units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
        value = timeframe.strip().lower()
        if len(value) < 2 or value[-1] not in units:
            raise HTTPException(status_code=400, detail="timeframe must be like 1m, 5m, 1h, 1d")
        return int(value[:-1]) * units[value[-1]]

    async def backfill_historical_candles(self, request: HistoricalCandlesRequest) -> dict[str, Any]:
        symbols = [symbol.upper() for symbol in parse_csv(request.symbols or self.settings.symbols)]
        exchanges = [exchange.lower() for exchange in parse_csv(request.exchanges or self.settings.futures_exchanges)]
        if not symbols or not exchanges:
            raise HTTPException(status_code=400, detail="symbols and exchanges are required")
        if self._should_use_common_futures(symbols):
            symbols = list(self.futures_base_symbols)
            if not symbols:
                symbols = await self._discover_common_futures_symbols(exchanges)
        if not symbols:
            raise HTTPException(status_code=400, detail="no futures symbols found")
        timeframe_ms = self._timeframe_ms(request.timeframe)
        if request.start_date:
            start_dt = datetime.fromisoformat(request.start_date).replace(tzinfo=timezone.utc)
            end_dt = datetime.fromisoformat(request.end_date or request.start_date).replace(tzinfo=timezone.utc)
            if request.end_date == "" or end_dt <= start_dt:
                end_dt = start_dt + timedelta(days=1)
            start_ms = int(start_dt.timestamp() * 1000)
            end_ms = int(end_dt.timestamp() * 1000)
        else:
            end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            start_ms = end_ms - (request.days * 86_400_000)
        wanted_limit = min(request.limit_per_market, max(50, int((end_ms - start_ms) / timeframe_ms)))
        results = []
        total_candles = 0
        started = datetime.now(timezone.utc)
        self.historical_candle_status = {
            "status": "running",
            "started_at": started,
            "timeframe": request.timeframe,
            "days": request.days,
            "markets": len(symbols) * len(exchanges),
        }
        for exchange_id in exchanges:
            for symbol in symbols:
                try:
                    candles = await self._fetch_futures_candles_paginated(
                        exchange_id,
                        symbol,
                        request.timeframe,
                        start_ms,
                        end_ms,
                        wanted_limit,
                        timeframe_ms,
                    )
                    total_candles += len(candles)
                    item = {
                        "timestamp": datetime.now(timezone.utc),
                        "exchange_id": exchange_id,
                        "symbol": symbol,
                        "timeframe": request.timeframe,
                        "days": request.days,
                        "count": len(candles),
                        "candles": candles,
                    }
                    append_jsonl(HISTORICAL_CANDLE_LOG_PATH, item)
                    results.append({"exchange_id": exchange_id, "symbol": symbol, "status": "ok", "count": len(candles)})
                except Exception as exc:
                    results.append({"exchange_id": exchange_id, "symbol": symbol, "status": "error", "message": f"{type(exc).__name__}: {exc}"})
        ok_count = sum(1 for item in results if item["status"] == "ok")
        error_count = len(results) - ok_count
        self.historical_candle_status = {
            "status": "done",
            "started_at": started,
            "finished_at": datetime.now(timezone.utc),
            "timeframe": request.timeframe,
            "days": request.days,
            "market_count": len(results),
            "ok_count": ok_count,
            "error_count": error_count,
            "candle_count": total_candles,
            "results": results[-80:],
            "file": str(HISTORICAL_CANDLE_LOG_PATH),
        }
        self.log("history", f"Historical candles saved: {total_candles} candles / {ok_count} markets")
        return to_jsonable(self.historical_candle_status)

    async def _fetch_futures_candles_paginated(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
        limit: int,
        timeframe_ms: int,
    ) -> list[dict[str, Any]]:
        all_candles: dict[int, dict[str, Any]] = {}
        cursor = start_ms
        hard_page_limit = 80
        for _ in range(hard_page_limit):
            if cursor >= end_ms:
                break
            page_end = min(end_ms, cursor + (limit * timeframe_ms))
            page = await self._fetch_futures_candles(exchange_id, symbol, timeframe, cursor, page_end, limit)
            for candle in page:
                try:
                    all_candles[int(candle["time"])] = candle
                except Exception:
                    continue
            if not page:
                cursor = page_end + timeframe_ms
            else:
                max_time = max(int(candle["time"]) for candle in page if candle.get("time") is not None)
                cursor = max(max_time + timeframe_ms, page_end + timeframe_ms)
            await asyncio.sleep(0.08)
        return [all_candles[key] for key in sorted(all_candles)]

    async def _fetch_futures_candles(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        compact = symbol.replace("/", "").replace("-", "").upper()
        headers = {"User-Agent": "arb-bot/1.0"}
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            if exchange_id == "binance":
                url = "https://fapi.binance.com/fapi/v1/klines"
                params = {"symbol": compact, "interval": timeframe, "startTime": start_ms, "endTime": end_ms, "limit": limit}
                async with session.get(url, params=params, timeout=15) as response:
                    response.raise_for_status()
                    data = await response.json()
                rows = data
            elif exchange_id == "okx":
                url = "https://www.okx.com/api/v5/market/history-candles"
                params = {"instId": symbol.replace("/", "-").upper() + "-SWAP", "bar": timeframe, "limit": min(limit, 300)}
                async with session.get(url, params=params, timeout=15) as response:
                    response.raise_for_status()
                    data = await response.json()
                rows = data.get("data") or []
            elif exchange_id == "bitget":
                url = "https://api.bitget.com/api/v2/mix/market/history-candles"
                params = {
                    "symbol": compact,
                    "productType": "USDT-FUTURES",
                    "granularity": timeframe,
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": min(limit, 200),
                }
                async with session.get(url, params=params, timeout=15) as response:
                    response.raise_for_status()
                    data = await response.json()
                rows = data.get("data") or []
            elif exchange_id == "hyperliquid":
                url = "https://api.hyperliquid.xyz/info"
                payload = {
                    "type": "candleSnapshot",
                    "req": {"coin": symbol.split("/")[0].upper(), "interval": timeframe, "startTime": start_ms, "endTime": end_ms},
                }
                async with session.post(url, json=payload, timeout=15) as response:
                    response.raise_for_status()
                    rows = await response.json()
            else:
                raise ValueError(f"unsupported futures exchange: {exchange_id}")
        return [self._normalize_candle(exchange_id, row) for row in rows if row]

    def _normalize_candle(self, exchange_id: str, row: Any) -> dict[str, Any]:
        if exchange_id == "hyperliquid":
            return {
                "time": row.get("t"),
                "open": row.get("o"),
                "high": row.get("h"),
                "low": row.get("l"),
                "close": row.get("c"),
                "volume": row.get("v"),
            }
        values = list(row)
        return {
            "time": values[0],
            "open": values[1],
            "high": values[2],
            "low": values[3],
            "close": values[4],
            "volume": values[5] if len(values) > 5 else None,
        }

    async def _record_relative_market_data(self, exchanges: list[str], symbols: list[str], quotes: list[dict[str, Any]]) -> None:
        interval = float(os.getenv("RELATIVE_MARKET_DATA_SECONDS", "60"))
        now = datetime.now(timezone.utc)
        if self.last_relative_market_data_at and (now - self.last_relative_market_data_at).total_seconds() < interval:
            return
        self.last_relative_market_data_at = now
        quote_map = {(quote.get("exchange_id"), quote.get("symbol")): quote for quote in quotes if quote.get("status") == "ok"}
        tasks = [
            self._fetch_relative_market_row(exchange_id, symbol, quote_map.get((exchange_id, symbol)))
            for exchange_id in exchanges
            for symbol in symbols
        ]
        rows = [row for row in await asyncio.gather(*tasks, return_exceptions=True) if isinstance(row, dict)]
        item = {"timestamp": now, "rows": rows}
        self.relative_market_history.append(to_jsonable(item))
        append_jsonl(RELATIVE_MARKET_DATA_LOG_PATH, item)

    async def _fetch_relative_market_row(self, exchange_id: str, symbol: str, quote: dict[str, Any] | None) -> dict[str, Any]:
        row: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc),
            "exchange_id": exchange_id,
            "symbol": symbol,
            "futures_symbol": self._futures_symbol(exchange_id, symbol),
            "status": "ok",
            "ohlcv_timeframe": "1m",
            "open_interest": None,
            "funding_rate": None,
            "liquidation": None,
        }
        if quote:
            bids = quote.get("bids") or []
            asks = quote.get("asks") or []
            row.update(
                {
                    "bid": quote.get("bid"),
                    "ask": quote.get("ask"),
                    "mid": quote.get("mid"),
                    "spread_pct": ((Decimal(str(quote["ask"])) - Decimal(str(quote["bid"]))) / Decimal(str(quote["mid"])) * Decimal("100"))
                    if quote.get("bid") and quote.get("ask") and quote.get("mid")
                    else None,
                    "bid_depth_quote": quote.get("bid_capacity_quote"),
                    "ask_depth_quote": quote.get("ask_capacity_quote"),
                    "orderbook_bids": bids[: int(os.getenv("RELATIVE_ORDERBOOK_LOG_LEVELS", "5"))],
                    "orderbook_asks": asks[: int(os.getenv("RELATIVE_ORDERBOOK_LOG_LEVELS", "5"))],
                }
            )
        try:
            end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            candles = await self._fetch_futures_candles(exchange_id, symbol, "1m", end_ms - 10 * 60_000, end_ms, 10)
            if candles:
                candle = candles[-1]
                row.update(
                    {
                        "ohlcv": candle,
                        "ohlcv_close": candle.get("close"),
                        "ohlcv_volume": candle.get("volume"),
                    }
                )
        except Exception as exc:
            row["ohlcv_error"] = f"{type(exc).__name__}: {exc}"
        try:
            metrics = await self._fetch_futures_market_metrics(exchange_id, symbol)
            row.update(metrics)
        except Exception as exc:
            row["metrics_error"] = f"{type(exc).__name__}: {exc}"
        return row

    async def _fetch_futures_market_metrics(self, exchange_id: str, symbol: str) -> dict[str, Any]:
        compact = symbol.replace("/", "").replace("-", "").upper()
        headers = {"User-Agent": "arb-bot/1.0"}
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            if exchange_id == "binance":
                metrics: dict[str, Any] = {}
                async with session.get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": compact}, timeout=10) as response:
                    response.raise_for_status()
                    oi = await response.json()
                metrics["open_interest"] = oi.get("openInterest")
                async with session.get("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": compact}, timeout=10) as response:
                    response.raise_for_status()
                    funding = await response.json()
                metrics["funding_rate"] = Decimal(str(funding.get("lastFundingRate") or 0)) * Decimal("100")
                return metrics
            if exchange_id == "okx":
                inst_id = symbol.replace("/", "-").upper() + "-SWAP"
                metrics = {}
                async with session.get("https://www.okx.com/api/v5/public/open-interest", params={"instType": "SWAP", "instId": inst_id}, timeout=10) as response:
                    response.raise_for_status()
                    oi = await response.json()
                oi_row = (oi.get("data") or [{}])[0]
                metrics["open_interest"] = oi_row.get("oi")
                async with session.get("https://www.okx.com/api/v5/public/funding-rate", params={"instId": inst_id}, timeout=10) as response:
                    response.raise_for_status()
                    funding = await response.json()
                funding_row = (funding.get("data") or [{}])[0]
                metrics["funding_rate"] = Decimal(str(funding_row.get("fundingRate") or 0)) * Decimal("100")
                return metrics
            if exchange_id == "hyperliquid":
                coin = symbol.split("/")[0].upper()
                async with session.post("https://api.hyperliquid.xyz/info", json={"type": "metaAndAssetCtxs"}, timeout=10) as response:
                    response.raise_for_status()
                    data = await response.json()
                universe = ((data or [None, None])[0] or {}).get("universe") or []
                ctxs = (data or [None, []])[1] or []
                for index, market in enumerate(universe):
                    if market.get("name") == coin and index < len(ctxs):
                        ctx = ctxs[index]
                        return {
                            "open_interest": ctx.get("openInterest"),
                            "funding_rate": Decimal(str(ctx.get("funding") or 0)) * Decimal("100"),
                        }
                return {}
            if exchange_id == "bitget":
                metrics = {}
                async with session.get(
                    "https://api.bitget.com/api/v2/mix/market/open-interest",
                    params={"symbol": compact, "productType": "USDT-FUTURES"},
                    timeout=10,
                ) as response:
                    response.raise_for_status()
                    oi = await response.json()
                oi_data = oi.get("data")
                if isinstance(oi_data, dict):
                    metrics["open_interest"] = oi_data.get("openInterestList", [{}])[0].get("size") if isinstance(oi_data.get("openInterestList"), list) else oi_data.get("openInterest")
                async with session.get(
                    "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
                    params={"symbol": compact, "productType": "USDT-FUTURES"},
                    timeout=10,
                ) as response:
                    response.raise_for_status()
                    funding = await response.json()
                funding_data = funding.get("data")
                if isinstance(funding_data, list):
                    funding_data = funding_data[0] if funding_data else {}
                if isinstance(funding_data, dict):
                    metrics["funding_rate"] = Decimal(str(funding_data.get("fundingRate") or 0)) * Decimal("100")
                return metrics
        return {}

    def set_demo_price_adjustment(self, request: DemoPriceAdjustment) -> None:
        key = (request.exchange_id.strip().lower(), request.symbol.strip().upper())
        bid_adjust = Decimal(str(request.bid_adjust_pct))
        ask_adjust = Decimal(str(request.ask_adjust_pct))
        if bid_adjust == 0 and ask_adjust == 0:
            self.demo_price_adjustments.pop(key, None)
            return
        self.demo_price_adjustments[key] = {
            "bid_adjust_pct": bid_adjust,
            "ask_adjust_pct": ask_adjust,
        }

    def clear_demo_price_adjustments(self) -> None:
        self.demo_price_adjustments.clear()

    def _apply_demo_price_adjustment(self, quote: Quote) -> Quote:
        adjustment = self.demo_price_adjustments.get((quote.exchange_id, quote.symbol))
        if not adjustment:
            return quote
        bid_factor = Decimal("1") + (adjustment["bid_adjust_pct"] / Decimal("100"))
        ask_factor = Decimal("1") + (adjustment["ask_adjust_pct"] / Decimal("100"))
        return Quote(
            exchange_id=quote.exchange_id,
            symbol=quote.symbol,
            bid=quote.bid * bid_factor,
            ask=quote.ask * ask_factor,
            bid_volume=quote.bid_volume,
            ask_volume=quote.ask_volume,
            taker_fee_pct=quote.taker_fee_pct,
            timestamp=quote.timestamp,
        )

    def _status_with_demo_adjustment(self, status: dict[str, Any], config: Config) -> dict[str, Any]:
        if config.mode != "demo" or status.get("status") != "ok":
            return status
        key = (status.get("exchange_id"), status.get("symbol"))
        adjustment = self.demo_price_adjustments.get(key)
        if not adjustment:
            return status
        adjusted = dict(status)
        bid_factor = Decimal("1") + (adjustment["bid_adjust_pct"] / Decimal("100"))
        ask_factor = Decimal("1") + (adjustment["ask_adjust_pct"] / Decimal("100"))
        adjusted["bid"] = Decimal(str(status["bid"])) * bid_factor
        adjusted["ask"] = Decimal(str(status["ask"])) * ask_factor
        adjusted["message"] = (
            f"繝・Δ萓｡譬ｼ謫堺ｽ・bid {adjustment['bid_adjust_pct']}%, ask {adjustment['ask_adjust_pct']}%"
        )
        return adjusted

    async def _find_optimized_opportunities(
        self,
        exchanges,
        config: Config,
        scan_sizes: list[Decimal],
        visible_quotes: list[Quote],
    ) -> list[Opportunity]:
        if len(scan_sizes) == 1:
            return find_opportunities(
                visible_quotes,
                scan_sizes[0],
                config.min_net_profit_pct,
                config.slippage_pct,
            )

        all_opportunities: list[Opportunity] = []
        for size in scan_sizes:
            quotes = await asyncio.gather(
                *[
                    fetch_quote(exchange, symbol, size, config.default_taker_fee_pct, config.orderbook_limit)
                    for exchange in exchanges
                    for symbol in config.symbols
                ]
            )
            if config.mode == "demo":
                quotes = [
                    self._apply_demo_price_adjustment(quote)
                    for quote in quotes
                    if quote is not None
                ]
            all_opportunities.extend(
                find_opportunities(
                    quotes,
                    size,
                    config.min_net_profit_pct,
                    config.slippage_pct,
                )
            )

        best_by_route: dict[tuple[str, str, str], Opportunity] = {}
        for opportunity in all_opportunities:
            key = (opportunity.symbol, opportunity.buy_exchange, opportunity.sell_exchange)
            current = best_by_route.get(key)
            if current is None or opportunity.estimated_profit_quote > current.estimated_profit_quote:
                best_by_route[key] = opportunity

        return sorted(best_by_route.values(), key=lambda item: item.estimated_profit_quote, reverse=True)

    async def _fetch_market_status(self, exchange, symbol: str, config: Config, quote_size: Decimal | None = None) -> dict[str, Any]:
        try:
            quote = await fetch_quote(
                exchange,
                symbol,
                quote_size or config.trade_size_quote,
                config.default_taker_fee_pct,
                config.orderbook_limit,
            )
            fee = getattr(exchange, "_arb_taker_fee_pct_by_symbol", {}).get(symbol, config.default_taker_fee_pct)
            fee_source = getattr(exchange, "_arb_fee_source_by_symbol", {}).get(symbol, "fallback")
            if quote is None:
                quote_error = getattr(exchange, "_arb_last_quote_error", None)
                return {
                    "quote": None,
                    "status": {
                        "exchange_id": exchange.id,
                        "symbol": symbol,
                        "status": "error" if quote_error else "no_quote",
                        "message": quote_error or "譚ｿ縺ｾ縺溘・豬∝虚諤ｧ縺御ｸ崎ｶｳ",
                        "taker_fee_pct": fee,
                        "fee_source": fee_source,
                    },
                }
            return {
                "quote": quote,
                "status": {
                    "exchange_id": exchange.id,
                    "symbol": symbol,
                    "status": "ok",
                        "message": "Fetched",
                    "bid": quote.bid,
                    "ask": quote.ask,
                    "bid_volume": quote.bid_volume,
                    "ask_volume": quote.ask_volume,
                    "taker_fee_pct": quote.taker_fee_pct,
                    "fee_source": fee_source,
                    "timestamp": quote.timestamp,
                },
            }
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self.log("warn", f"{exchange.id} {symbol}: {message}")
            return {
                "quote": None,
                "status": {
                    "exchange_id": exchange.id,
                    "symbol": symbol,
                    "status": "error",
                    "message": message,
                    "taker_fee_pct": getattr(exchange, "_arb_taker_fee_pct_by_symbol", {}).get(
                        symbol, config.default_taker_fee_pct
                    ),
                },
            }

    async def _execute(self, opportunity: Opportunity, config: Config, exchanges) -> None:
        if config.mode == "demo":
            trade = self.demo.execute(opportunity, "demo")
            if trade:
                self.log("fill", f"DEMO fill {opportunity.symbol}: +{opportunity.estimated_profit_quote:.4f}")
            return

        max_live_quote = Decimal(os.getenv("MAX_LIVE_TRADE_QUOTE", "25"))
        if opportunity.quote_amount > max_live_quote:
            self.log("live", f"譛ｬ逡ｪ逋ｺ豕ｨ繧ｹ繧ｭ繝・・: quote size {opportunity.quote_amount:.4f} > MAX_LIVE_TRADE_QUOTE {max_live_quote}")
            return

        exchange_by_id = {exchange.id: exchange for exchange in exchanges}
        buy_exchange = exchange_by_id.get(opportunity.buy_exchange)
        sell_exchange = exchange_by_id.get(opportunity.sell_exchange)
        if not buy_exchange or not sell_exchange:
            self.log("live", "譛ｬ逡ｪ逋ｺ豕ｨ繧ｹ繧ｭ繝・・: exchange handle not found")
            return

        try:
            amount = Decimal(
                buy_exchange.amount_to_precision(opportunity.symbol, float(opportunity.base_amount))
            )
            if amount <= 0:
                self.log("live", "譛ｬ逡ｪ逋ｺ豕ｨ繧ｹ繧ｭ繝・・: amount precision rounded to zero")
                return

            buy_order, sell_order = await asyncio.gather(
                buy_exchange.create_order(opportunity.symbol, "market", "buy", float(amount)),
                sell_exchange.create_order(opportunity.symbol, "market", "sell", float(amount)),
            )
            self.log(
                "live",
                f"LIVE orders sent {opportunity.symbol}: buy {buy_exchange.id} / sell {sell_exchange.id} amount {amount}",
            )
            self.demo.trades.appendleft(
                to_jsonable(
                    trade := {
                        "timestamp": datetime.now(timezone.utc),
                        "symbol": opportunity.symbol,
                        "buy_exchange": opportunity.buy_exchange,
                        "sell_exchange": opportunity.sell_exchange,
                        "buy_price": opportunity.buy_price,
                        "sell_price": opportunity.sell_price,
                        "base_amount": amount,
                        "quote_amount": opportunity.quote_amount,
                        "net_profit_pct": opportunity.net_profit_pct,
                        "profit_quote": opportunity.estimated_profit_quote,
                        "mode": "live",
                        "status": f"orders_sent buy={buy_order.get('id')} sell={sell_order.get('id')}",
                    }
                )
            )
            append_jsonl(TRADE_LOG_PATH, trade)
        except Exception as exc:
            self.log("error", f"譛ｬ逡ｪ逋ｺ豕ｨ螟ｱ謨・ {type(exc).__name__}: {exc}")

    def _book_capacity_quote(self, levels: list) -> Decimal:
        total = Decimal("0")
        for price_raw, size_raw, *_ in levels:
            try:
                price = Decimal(str(price_raw))
                size = Decimal(str(size_raw))
            except Exception:
                continue
            if price > 0 and size > 0:
                total += price * size
        return total

    def _weighted_futures_price(self, levels: list, quote_amount: Decimal) -> tuple[Decimal, Decimal] | None:
        remaining = quote_amount
        total_quote = Decimal("0")
        total_base = Decimal("0")
        for price_raw, size_raw, *_ in levels:
            try:
                price = Decimal(str(price_raw))
                size = Decimal(str(size_raw))
            except Exception:
                continue
            if price <= 0 or size <= 0:
                continue
            level_quote = price * size
            use_quote = min(remaining, level_quote)
            total_quote += use_quote
            total_base += use_quote / price
            remaining -= use_quote
            if remaining <= 0:
                break
        if total_base <= 0 or total_quote < quote_amount:
            return None
        return total_quote / total_base, total_base

    def _futures_taker_fee_pct(self, exchange_id: str) -> Decimal:
        defaults = {"binance": "0.05", "hyperliquid": "0.045", "okx": "0.05", "bitget": "0.06"}
        return Decimal(os.getenv(f"FUTURES_FEE_{exchange_id.upper()}_PCT", defaults.get(exchange_id, "0.06")))

    def _futures_round_trip_cost_pct(self, long_exchange: str, short_exchange: str, config: Config) -> Decimal:
        fees = (self._futures_taker_fee_pct(long_exchange) + self._futures_taker_fee_pct(short_exchange)) * Decimal("2")
        slippage = config.slippage_pct * Decimal("4")
        one_sided_buffer = Decimal(os.getenv("FUTURES_ONE_SIDED_RISK_BUFFER_PCT", "0.03"))
        return fees + slippage + one_sided_buffer

    def _record_relative_snapshot(self, quotes: list[dict[str, Any]]) -> None:
        by_symbol: dict[str, list[Decimal]] = {}
        liquidity_by_symbol: dict[str, Decimal] = {}
        bid_liquidity_by_symbol: dict[str, Decimal] = {}
        ask_liquidity_by_symbol: dict[str, Decimal] = {}
        spread_by_symbol: dict[str, list[Decimal]] = {}
        bid_by_symbol: dict[str, list[Decimal]] = {}
        ask_by_symbol: dict[str, list[Decimal]] = {}
        for quote in quotes:
            by_symbol.setdefault(quote["symbol"], []).append(Decimal(str(quote["mid"])))
            bid = Decimal(str(quote.get("bid") or 0))
            ask = Decimal(str(quote.get("ask") or 0))
            if bid > 0 and ask > 0:
                bid_by_symbol.setdefault(quote["symbol"], []).append(bid)
                ask_by_symbol.setdefault(quote["symbol"], []).append(ask)
                spread_by_symbol.setdefault(quote["symbol"], []).append(((ask - bid) / ((ask + bid) / Decimal("2"))) * Decimal("100"))
            bid_cap = Decimal(str(quote.get("bid_capacity_quote") or 0))
            ask_cap = Decimal(str(quote.get("ask_capacity_quote") or 0))
            capacity = min(bid_cap, ask_cap)
            liquidity_by_symbol[quote["symbol"]] = liquidity_by_symbol.get(quote["symbol"], Decimal("0")) + capacity
            bid_liquidity_by_symbol[quote["symbol"]] = bid_liquidity_by_symbol.get(quote["symbol"], Decimal("0")) + bid_cap
            ask_liquidity_by_symbol[quote["symbol"]] = ask_liquidity_by_symbol.get(quote["symbol"], Decimal("0")) + ask_cap
        mids = {
            symbol: sum(values) / Decimal(str(len(values)))
            for symbol, values in by_symbol.items()
            if values
        }
        if not mids:
            return
        spreads = {
            symbol: sum(values) / Decimal(str(len(values)))
            for symbol, values in spread_by_symbol.items()
            if values
        }
        bids = {
            symbol: sum(values) / Decimal(str(len(values)))
            for symbol, values in bid_by_symbol.items()
            if values
        }
        asks = {
            symbol: sum(values) / Decimal(str(len(values)))
            for symbol, values in ask_by_symbol.items()
            if values
        }
        snapshot = {
            "timestamp": datetime.now(timezone.utc),
            "mids": mids,
            "bids": bids,
            "asks": asks,
            "spread_pct": spreads,
            "liquidity_quote": liquidity_by_symbol,
            "bid_liquidity_quote": bid_liquidity_by_symbol,
            "ask_liquidity_quote": ask_liquidity_by_symbol,
        }
        self.relative_history.append(to_jsonable(snapshot))
        self.relative_rankings = self._build_relative_rankings()
        feature_item = {
            "timestamp": datetime.now(timezone.utc),
            "features": [self._slim_learning_feature(row) for row in self.relative_rankings.get("features", [])],
        }
        self.relative_feature_history.append(to_jsonable(feature_item))
        append_jsonl(RELATIVE_FEATURE_LOG_PATH, feature_item)

    def _slim_learning_feature(self, row: dict[str, Any]) -> dict[str, Any]:
        heavy_keys = {
            "volume_since_9jst",
            "price_return_since_9jst_series",
            "price_return_since_9jst_times",
            "price_candles",
        }
        return {key: value for key, value in row.items() if key not in heavy_keys}

    def _relative_returns(self, lookback_minutes: int = 60) -> list[dict[str, Any]]:
        if len(self.relative_history) < 2:
            return []
        latest = self.relative_history[-1]
        latest_time = datetime.fromisoformat(latest["timestamp"])
        target_age = lookback_minutes * 60
        base = self.relative_history[0]
        for item in reversed(self.relative_history):
            item_time = datetime.fromisoformat(item["timestamp"])
            if (latest_time - item_time).total_seconds() >= target_age:
                base = item
                break
        rows = []
        latest_mids = latest.get("mids", {})
        base_mids = base.get("mids", {})
        for symbol, latest_mid in latest_mids.items():
            old_mid = base_mids.get(symbol)
            if old_mid is None:
                continue
            old = Decimal(str(old_mid))
            new = Decimal(str(latest_mid))
            if old <= 0:
                continue
            rows.append({"symbol": symbol, "return_pct": ((new - old) / old) * Decimal("100")})
        return sorted(rows, key=lambda item: item["return_pct"], reverse=True)

    def _relative_return_pct(self, symbol: str, lookback_minutes: int) -> Decimal | None:
        if len(self.relative_history) < 2:
            return None
        latest = self.relative_history[-1]
        latest_time = datetime.fromisoformat(latest["timestamp"])
        target_age = lookback_minutes * 60
        base = self.relative_history[0]
        for item in reversed(self.relative_history):
            item_time = datetime.fromisoformat(item["timestamp"])
            if (latest_time - item_time).total_seconds() >= target_age:
                base = item
                break
        latest_mid = latest.get("mids", {}).get(symbol)
        base_mid = base.get("mids", {}).get(symbol)
        if latest_mid is None or base_mid is None:
            return None
        old = Decimal(str(base_mid))
        new = Decimal(str(latest_mid))
        if old <= 0:
            return None
        return ((new - old) / old) * Decimal("100")

    def _relative_volatility_pct(self, symbol: str, lookback_points: int = 80) -> Decimal:
        points = [item for item in list(self.relative_history)[-lookback_points:] if symbol in item.get("mids", {})]
        if len(points) < 3:
            return Decimal("1")
        returns = []
        previous = Decimal(str(points[0]["mids"][symbol]))
        for item in points[1:]:
            current = Decimal(str(item["mids"][symbol]))
            if previous > 0 and current > 0:
                returns.append(abs(((current - previous) / previous) * Decimal("100")))
            previous = current
        if not returns:
            return Decimal("1")
        average = sum(returns) / Decimal(str(len(returns)))
        return max(average, Decimal("0.01"))

    def _relative_sizing_volatility_pct(self, symbol: str) -> Decimal:
        live_vol = self._relative_volatility_pct(symbol)
        if live_vol != Decimal("1"):
            return live_vol
        candles = self._historical_candles_for_symbol(symbol, max_bars=80).get("candles", [])
        closes = []
        for candle in candles:
            try:
                close = Decimal(str(candle.get("close")))
                if close > 0:
                    closes.append(close)
            except Exception:
                continue
        if len(closes) >= 5:
            returns = [
                abs(((current - previous) / previous) * Decimal("100"))
                for previous, current in zip(closes[-41:-1], closes[-40:])
                if previous > 0 and current > 0
            ]
            if returns:
                return max(sum(returns) / Decimal(str(len(returns))), Decimal("0.05"))
        base = symbol.split("/")[0].upper()
        if base == "BTC":
            return Decimal(os.getenv("RELATIVE_DEFAULT_BTC_VOL_PCT", "0.35"))
        if base == "ETH":
            return Decimal(os.getenv("RELATIVE_DEFAULT_ETH_VOL_PCT", "0.60"))
        if base in {"SOL", "BNB", "XRP", "DOGE", "HYPE"}:
            return Decimal(os.getenv("RELATIVE_DEFAULT_MAJOR_ALT_VOL_PCT", "1.00"))
        return Decimal(os.getenv("RELATIVE_DEFAULT_ALT_VOL_PCT", "1.50"))

    def _relative_series(self, symbol: str, limit: int = 120) -> list[Decimal]:
        rows = [item for item in list(self.relative_history)[-limit:] if symbol in item.get("mids", {})]
        return [Decimal(str(item["mids"][symbol])) for item in rows]

    def _ema(self, values: list[Decimal], period: int) -> Decimal | None:
        if len(values) < period:
            return None
        multiplier = Decimal("2") / Decimal(str(period + 1))
        ema = sum(values[:period]) / Decimal(str(period))
        for value in values[period:]:
            ema = (value - ema) * multiplier + ema
        return ema

    def _rsi(self, values: list[Decimal], period: int = 14) -> Decimal | None:
        if len(values) <= period:
            return None
        gains: list[Decimal] = []
        losses: list[Decimal] = []
        for previous, current in zip(values[-(period + 1):-1], values[-period:]):
            change = current - previous
            if change >= 0:
                gains.append(change)
                losses.append(Decimal("0"))
            else:
                gains.append(Decimal("0"))
                losses.append(abs(change))
        average_gain = sum(gains) / Decimal(str(period))
        average_loss = sum(losses) / Decimal(str(period))
        if average_loss == 0:
            return Decimal("100")
        rs = average_gain / average_loss
        return Decimal("100") - (Decimal("100") / (Decimal("1") + rs))

    def _atr_pct(self, values: list[Decimal], period: int = 14) -> Decimal | None:
        if len(values) <= period:
            return None
        ranges = []
        for previous, current in zip(values[-(period + 1):-1], values[-period:]):
            if previous > 0:
                ranges.append(abs((current - previous) / previous) * Decimal("100"))
        if not ranges:
            return None
        return sum(ranges) / Decimal(str(len(ranges)))

    def _relative_return_from_jst_9(self, symbol: str) -> Decimal | None:
        if len(self.relative_history) < 2:
            return None
        latest = self.relative_history[-1]
        latest_time = datetime.fromisoformat(latest["timestamp"])
        jst = timezone(timedelta(hours=9))
        latest_jst = latest_time.astimezone(jst)
        start_jst = latest_jst.replace(hour=9, minute=0, second=0, microsecond=0)
        if latest_jst < start_jst:
            start_jst -= timedelta(days=1)
        base = None
        for item in reversed(self.relative_history):
            item_time = datetime.fromisoformat(item["timestamp"]).astimezone(jst)
            if item_time <= start_jst and symbol in item.get("mids", {}):
                base = item
                break
        if base is None:
            base = next((item for item in self.relative_history if symbol in item.get("mids", {})), None)
        if base is None or symbol not in latest.get("mids", {}):
            return None
        old = Decimal(str(base["mids"][symbol]))
        new = Decimal(str(latest["mids"][symbol]))
        if old <= 0:
            return None
        return ((new - old) / old) * Decimal("100")

    def _liquidity_growth_pct(self, symbol: str, lookback_points: int = 60) -> Decimal | None:
        points = [
            item for item in list(self.relative_history)[-lookback_points:]
            if symbol in item.get("liquidity_quote", {})
        ]
        if len(points) < 2:
            return None
        old = Decimal(str(points[0]["liquidity_quote"][symbol]))
        new = Decimal(str(points[-1]["liquidity_quote"][symbol]))
        if old <= 0:
            return None
        return ((new - old) / old) * Decimal("100")

    def _liquidity_growth_minutes(self, symbol: str, lookback_minutes: int) -> Decimal | None:
        if len(self.relative_history) < 2:
            return None
        latest = self.relative_history[-1]
        latest_time = datetime.fromisoformat(latest["timestamp"])
        target_age = lookback_minutes * 60
        base = self.relative_history[0]
        for item in reversed(self.relative_history):
            item_time = datetime.fromisoformat(item["timestamp"])
            if (latest_time - item_time).total_seconds() >= target_age:
                base = item
                break
        latest_liquidity = latest.get("liquidity_quote", {}).get(symbol)
        base_liquidity = base.get("liquidity_quote", {}).get(symbol)
        if latest_liquidity is None or base_liquidity is None:
            return None
        old = Decimal(str(base_liquidity))
        new = Decimal(str(latest_liquidity))
        if old <= 0:
            return None
        return ((new - old) / old) * Decimal("100")

    def _depth_growth_minutes(self, symbol: str, key: str, lookback_minutes: int) -> Decimal | None:
        if len(self.relative_history) < 2:
            return None
        latest = self.relative_history[-1]
        latest_time = datetime.fromisoformat(latest["timestamp"])
        target_age = lookback_minutes * 60
        base = self.relative_history[0]
        for item in reversed(self.relative_history):
            item_time = datetime.fromisoformat(item["timestamp"])
            if (latest_time - item_time).total_seconds() >= target_age:
                base = item
                break
        latest_value = latest.get(key, {}).get(symbol)
        base_value = base.get(key, {}).get(symbol)
        if latest_value is None or base_value is None:
            return None
        old = Decimal(str(base_value))
        new = Decimal(str(latest_value))
        if old <= 0:
            return None
        return ((new - old) / old) * Decimal("100")

    def _latest_symbol_value(self, key: str, symbol: str, default: Decimal | None = None) -> Decimal | None:
        if not self.relative_history:
            return default
        value = self.relative_history[-1].get(key, {}).get(symbol)
        if value is None:
            return default
        return Decimal(str(value))

    def _latest_market_average(self, symbol: str, key: str) -> Decimal | None:
        for item in reversed(self.relative_market_history):
            values = []
            for row in item.get("rows", []):
                if row.get("symbol") != symbol or row.get(key) is None:
                    continue
                try:
                    values.append(Decimal(str(row[key])))
                except Exception:
                    continue
            if values:
                return sum(values) / Decimal(str(len(values)))
        return None

    def _market_metric_change_minutes(self, symbol: str, key: str, lookback_minutes: int) -> Decimal | None:
        if len(self.relative_market_history) < 2:
            return None
        latest = self.relative_market_history[-1]
        latest_time = datetime.fromisoformat(latest["timestamp"]) if isinstance(latest["timestamp"], str) else latest["timestamp"]
        target_age = lookback_minutes * 60
        base = self.relative_market_history[0]
        for item in reversed(self.relative_market_history):
            item_time = datetime.fromisoformat(item["timestamp"]) if isinstance(item["timestamp"], str) else item["timestamp"]
            if (latest_time - item_time).total_seconds() >= target_age:
                base = item
                break

        def average(rowset: dict[str, Any]) -> Decimal | None:
            values = []
            for row in rowset.get("rows", []):
                if row.get("symbol") != symbol or row.get(key) is None:
                    continue
                try:
                    values.append(Decimal(str(row[key])))
                except Exception:
                    continue
            return sum(values) / Decimal(str(len(values))) if values else None

        old = average(base)
        new = average(latest)
        if old is None or new is None or old <= 0:
            return None
        return ((new - old) / old) * Decimal("100")

    def _vwap(self, symbol: str, lookback_points: int = 240) -> Decimal | None:
        rows = [item for item in list(self.relative_history)[-lookback_points:] if symbol in item.get("mids", {})]
        if not rows:
            return None
        total_price_volume = Decimal("0")
        total_volume = Decimal("0")
        for item in rows:
            price = Decimal(str(item["mids"][symbol]))
            volume = Decimal(str((item.get("liquidity_quote") or {}).get(symbol, 0)))
            if price > 0 and volume > 0:
                total_price_volume += price * volume
                total_volume += volume
        if total_volume <= 0:
            values = [Decimal(str(item["mids"][symbol])) for item in rows]
            return sum(values) / Decimal(str(len(values)))
        return total_price_volume / total_volume

    def _historical_return_pct(self, symbol: str, lookback_hours: int) -> Decimal | None:
        item = self._load_historical_candle_cache().get(symbol.upper())
        candles = item.get("candles") if item else None
        if not candles or len(candles) < 2:
            return None
        parsed = []
        for raw in candles:
            try:
                parsed.append((int(raw["time"]), Decimal(str(raw["close"]))))
            except Exception:
                continue
        if len(parsed) < 2:
            return None
        parsed.sort(key=lambda value: value[0])
        latest_time, latest_close = parsed[-1]
        target = latest_time - (lookback_hours * 3_600_000)
        base_close = parsed[0][1]
        for candle_time, close in reversed(parsed):
            if candle_time <= target:
                base_close = close
                break
        if base_close <= 0:
            return None
        return ((latest_close - base_close) / base_close) * Decimal("100")

    def _liquidity_since_jst_9(self, symbol: str) -> list[Decimal]:
        if not self.relative_history:
            return []
        latest_time = datetime.fromisoformat(self.relative_history[-1]["timestamp"])
        jst = timezone(timedelta(hours=9))
        latest_jst = latest_time.astimezone(jst)
        start_jst = latest_jst.replace(hour=9, minute=0, second=0, microsecond=0)
        if latest_jst < start_jst:
            start_jst -= timedelta(days=1)
        values = []
        for item in self.relative_history:
            item_time = datetime.fromisoformat(item["timestamp"]).astimezone(jst)
            if item_time >= start_jst and symbol in item.get("liquidity_quote", {}):
                values.append(Decimal(str(item["liquidity_quote"][symbol])))
        return values[-240:]

    def _price_return_since_jst_9_chart(self, symbol: str) -> dict[str, Any]:
        if not self.relative_history:
            return {"values": [], "times": []}
        latest_time = datetime.fromisoformat(self.relative_history[-1]["timestamp"])
        jst = timezone(timedelta(hours=9))
        latest_jst = latest_time.astimezone(jst)
        start_jst = latest_jst.replace(hour=9, minute=0, second=0, microsecond=0)
        if latest_jst < start_jst:
            start_jst -= timedelta(days=1)
        base_price = None
        chart_start = max(start_jst, latest_jst - timedelta(hours=2))
        points: list[tuple[datetime, Decimal]] = []
        for item in self.relative_history:
            item_time = datetime.fromisoformat(item["timestamp"]).astimezone(jst)
            if item_time >= start_jst and symbol in item.get("mids", {}):
                price = Decimal(str(item["mids"][symbol]))
                if price > 0 and base_price is None:
                    base_price = price
                if price > 0 and item_time >= chart_start:
                    points.append((item_time, price))
        if not points or base_price is None:
            return {"values": [], "times": []}
        if base_price <= 0:
            return {"values": [], "times": []}
        series = [
            {"time": item_time.strftime("%H:%M"), "value": ((price - base_price) / base_price) * Decimal("100")}
            for item_time, price in points
        ]
        if len(series) <= 120:
            sampled = series
        else:
            step = len(series) / Decimal("120")
            sampled = []
            index = Decimal("0")
            while int(index) < len(series) and len(sampled) < 120:
                sampled.append(series[int(index)])
                index += step
            if sampled and sampled[-1] != series[-1]:
                sampled[-1] = series[-1]
        return {
            "values": [item["value"] for item in sampled],
            "times": [item["time"] for item in sampled],
        }

    def _load_historical_candle_cache(self) -> dict[str, dict[str, Any]]:
        if not HISTORICAL_CANDLE_LOG_PATH.exists():
            return {}
        mtime = HISTORICAL_CANDLE_LOG_PATH.stat().st_mtime
        if self._historical_candle_cache_mtime == mtime:
            return self._historical_candle_cache
        latest: dict[str, dict[str, Any]] = {}
        with HISTORICAL_CANDLE_LOG_PATH.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                symbol = str(item.get("symbol") or "").upper()
                candles = item.get("candles") or []
                if not symbol or not candles:
                    continue
                previous = latest.get(symbol)
                if previous is None:
                    latest[symbol] = item
                    continue
                previous_days = int(previous.get("days") or 0)
                item_days = int(item.get("days") or 0)
                previous_count = int(previous.get("count") or len(previous.get("candles") or []))
                item_count = int(item.get("count") or len(candles))
                if (item_days, item_count, str(item.get("timestamp") or "")) >= (
                    previous_days,
                    previous_count,
                    str(previous.get("timestamp") or ""),
                ):
                    latest[symbol] = item
        self._historical_candle_cache_mtime = mtime
        self._historical_candle_cache = latest
        return latest

    def _historical_candles_for_symbol(self, symbol: str, max_bars: int = 180) -> dict[str, Any]:
        item = self._load_historical_candle_cache().get(symbol.upper())
        raw_candles = item.get("candles") if item else None
        if not raw_candles:
            return {"candles": [], "timeframe": "", "days": 0, "exchange": ""}
        candles = []
        for raw in raw_candles:
            try:
                candles.append(
                    {
                        "time": int(raw["time"]),
                        "open": Decimal(str(raw["open"])),
                        "high": Decimal(str(raw["high"])),
                        "low": Decimal(str(raw["low"])),
                        "close": Decimal(str(raw["close"])),
                    }
                )
            except Exception:
                continue
        candles = [candle for candle in sorted(candles, key=lambda value: value["time"]) if candle["open"] > 0]
        if not candles:
            return {"candles": [], "timeframe": "", "days": 0, "exchange": ""}
        latest_time = candles[-1]["time"]
        month_start = latest_time - (30 * 86_400_000)
        candles = [candle for candle in candles if candle["time"] >= month_start]
        if len(candles) > max_bars:
            bucket_size = max(1, len(candles) // max_bars)
            bucketed = []
            for index in range(0, len(candles), bucket_size):
                bucket = candles[index : index + bucket_size]
                if not bucket:
                    continue
                bucketed.append(
                    {
                        "time": bucket[0]["time"],
                        "open": bucket[0]["open"],
                        "high": max(item["high"] for item in bucket),
                        "low": min(item["low"] for item in bucket),
                        "close": bucket[-1]["close"],
                    }
                )
            candles = bucketed[-max_bars:]
        jst = timezone(timedelta(hours=9))
        return {
            "candles": [
                {
                    "time": datetime.fromtimestamp(candle["time"] / 1000, tz=timezone.utc).astimezone(jst).strftime("%m/%d %H:%M"),
                    "open": candle["open"],
                    "high": candle["high"],
                    "low": candle["low"],
                    "close": candle["close"],
                }
                for candle in candles
            ],
            "timeframe": item.get("timeframe") or "",
            "days": min(int(item.get("days") or 0), 30),
            "exchange": item.get("exchange_id") or "",
        }

    def _smoothed_relative_score(self, symbol: str, current_score: Decimal, lookback_items: int = 30) -> Decimal:
        scores = [current_score]
        for item in list(self.relative_feature_history)[-lookback_items:]:
            for row in item.get("features", []):
                if row.get("symbol") == symbol and row.get("relative_score") is not None:
                    scores.append(Decimal(str(row["relative_score"])))
                    break
        return sum(scores) / Decimal(str(len(scores)))

    def _score_relative_features(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        max_liquidity = max(Decimal(str(row.get("liquidity_quote") or 0)) for row in rows) or Decimal("1")
        ranked_by_momentum = sorted(
            rows,
            key=lambda row: Decimal(str(row.get("return_1h_pct") or 0)) + Decimal(str(row.get("return_4h_pct") or 0)),
            reverse=True,
        )
        category_scores = {}
        if len(ranked_by_momentum) > 1:
            denominator = Decimal(str(len(ranked_by_momentum) - 1))
            for index, row in enumerate(ranked_by_momentum):
                category_scores[row["symbol"]] = Decimal("2") - (Decimal(str(index)) / denominator * Decimal("4"))
        def percentile_scores(field: str) -> dict[str, Decimal]:
            ranked = sorted(rows, key=lambda item: Decimal(str(item.get(field) or 0)))
            if len(ranked) <= 1:
                return {str(row["symbol"]): Decimal("0") for row in rows}
            denominator = Decimal(str(len(ranked) - 1))
            scores = {}
            for index, item in enumerate(ranked):
                scores[str(item["symbol"])] = (Decimal(str(index)) / denominator * Decimal("2")) - Decimal("1")
            return scores

        return_1h_scores = percentile_scores("return_1h_pct")
        return_4h_scores = percentile_scores("return_4h_pct")
        scored = []
        for row in rows:
            return_15m = Decimal(str(row.get("return_15m_pct") or 0))
            return_1h = Decimal(str(row.get("return_1h_pct") or 0))
            return_4h = Decimal(str(row.get("return_4h_pct") or 0))
            return_1h_score = return_1h_scores.get(str(row["symbol"]), Decimal("0"))
            return_4h_score = return_4h_scores.get(str(row["symbol"]), Decimal("0"))
            volume_15m = Decimal(str(row.get("volume_change_15m_pct") or 0))
            volume_1h = Decimal(str(row.get("volume_change_1h_pct") or 0))
            volume_4h = Decimal(str(row.get("volume_change_4h_pct") or 0))
            real_volume_15m = row.get("real_volume_change_15m_pct")
            real_volume_1h = row.get("real_volume_change_1h_pct")
            real_volume_4h = row.get("real_volume_change_4h_pct")
            score_volume_15m = Decimal(str(real_volume_15m if real_volume_15m is not None else volume_15m))
            score_volume_1h = Decimal(str(real_volume_1h if real_volume_1h is not None else volume_1h))
            score_volume_4h = Decimal(str(real_volume_4h if real_volume_4h is not None else volume_4h))
            bid_depth_1h = Decimal(str(row.get("bid_depth_change_1h_pct") or 0))
            ask_depth_1h = Decimal(str(row.get("ask_depth_change_1h_pct") or 0))
            bid_depth_4h = Decimal(str(row.get("bid_depth_change_4h_pct") or 0))
            ask_depth_4h = Decimal(str(row.get("ask_depth_change_4h_pct") or 0))
            oi_15m = Decimal(str(row.get("oi_change_15m_pct") or 0))
            oi_1h = Decimal(str(row.get("oi_change_1h_pct") or 0))
            oi_4h = Decimal(str(row.get("oi_change_4h_pct") or 0))
            oi_24h = Decimal(str(row.get("oi_change_24h_pct") or 0))
            funding = Decimal(str(row.get("funding_rate") or 0))
            rsi = Decimal(str(row.get("rsi") or 50))
            atr = Decimal(str(row.get("atr_pct") or 0))
            liquidity = Decimal(str(row.get("liquidity_quote") or 0))
            spread_pct = Decimal(str(row.get("spread_pct") or 0))
            latest_price = Decimal(str(row.get("vwap") or 0))
            vwap = Decimal(str(row.get("vwap_1h") or latest_price or 0))
            ema20 = Decimal(str(row.get("ema20") or latest_price or 0))
            vwap_position = Decimal("0") if vwap <= 0 or latest_price <= 0 else ((latest_price - vwap) / vwap) * Decimal("100")
            ema20_position = Decimal("0") if ema20 <= 0 or latest_price <= 0 else ((latest_price - ema20) / ema20) * Decimal("100")
            category_score = category_scores.get(row["symbol"], Decimal("0"))
            funding_overheat = max(Decimal("0"), abs(funding) - Decimal(os.getenv("RELATIVE_MAX_FUNDING_ABS", "0.08"))) * Decimal("25")
            rsi_overheat = max(Decimal("0"), rsi - Decimal(os.getenv("RELATIVE_RSI_OVERHEAT", "72"))) / Decimal("5")
            spread_penalty = max(Decimal("0"), spread_pct - Decimal(os.getenv("RELATIVE_MAX_SPREAD_PCT", "0.12"))) * Decimal("10")
            min_liquidity = Decimal(os.getenv("RELATIVE_MIN_LIQUIDITY_QUOTE", "10000"))
            low_liquidity_penalty = Decimal("2.0") if liquidity < min_liquidity else Decimal("0")
            oi_24h_overheat = max(Decimal("0"), oi_24h - Decimal(os.getenv("RELATIVE_MAX_24H_OI_CHANGE_PCT", "40"))) / Decimal("10")
            surge_1h_penalty = max(Decimal("0"), return_1h - Decimal(os.getenv("RELATIVE_MAX_1H_RETURN_PCT", "6"))) / Decimal("2")
            surge_4h_penalty = max(Decimal("0"), return_4h - Decimal(os.getenv("RELATIVE_MAX_4H_RETURN_PCT", "14"))) / Decimal("4")
            rsi_extreme_penalty = max(Decimal("0"), rsi - Decimal(os.getenv("RELATIVE_EXTREME_RSI", "80"))) / Decimal("4")
            vwap_divergence_penalty = max(
                Decimal("0"),
                abs(vwap_position) - Decimal(os.getenv("RELATIVE_MAX_VWAP_DIVERGENCE_PCT", "3")),
            ) / Decimal("2")
            price_surge_penalty = surge_1h_penalty + surge_4h_penalty + rsi_extreme_penalty + vwap_divergence_penalty
            cap = lambda value, limit: max(-limit, min(limit, value))
            depth_pressure = cap((bid_depth_1h - ask_depth_1h) / Decimal("10"), Decimal("2")) * Decimal("1.2")
            depth_pressure += cap((bid_depth_4h - ask_depth_4h) / Decimal("10"), Decimal("2")) * Decimal("0.8")
            score_parts = {
                "15m_return": cap(return_15m, Decimal("5")) * Decimal("0.5"),
                "1h_return_rank": cap(return_1h_score, Decimal("1")) * Decimal("0.75"),
                "4h_return_rank": cap(return_4h_score, Decimal("1")) * Decimal("1"),
                "15m_volume": cap(score_volume_15m / Decimal("10"), Decimal("4")) * Decimal("0.5"),
                "1h_volume": cap(score_volume_1h / Decimal("10"), Decimal("5")) * Decimal("0.8"),
                "4h_volume": cap(score_volume_4h / Decimal("10"), Decimal("5")) * Decimal("0.6"),
                "15m_oi": cap(oi_15m / Decimal("10"), Decimal("5")) * Decimal("1"),
                "1h_oi": cap(oi_1h / Decimal("10"), Decimal("5")) * Decimal("2"),
                "4h_oi": cap(oi_4h / Decimal("10"), Decimal("5")) * Decimal("2"),
                "bid_ask_depth_pressure": depth_pressure,
                "ema20_position": cap(ema20_position, Decimal("4")) * Decimal("1.5"),
                "relative_rank": category_score * Decimal("2"),
                "funding_overheat": -(funding_overheat * Decimal("2")),
                "rsi_overheat": -(rsi_overheat * Decimal("1")),
                "wide_spread": -(spread_penalty * Decimal("2")),
                "low_liquidity": -(low_liquidity_penalty * Decimal("2")),
                "24h_oi_overheat": -(oi_24h_overheat * Decimal("2")),
                "price_surge": -(price_surge_penalty * Decimal("2")),
            }
            score = sum(score_parts.values())
            exclusions = []
            if liquidity < min_liquidity:
                exclusions.append("low_liquidity")
            if spread_pct > Decimal(os.getenv("RELATIVE_MAX_SPREAD_PCT", "0.12")):
                exclusions.append("wide_spread")
            if abs(funding) > Decimal(os.getenv("RELATIVE_MAX_FUNDING_ABS", "0.08")):
                exclusions.append("funding_overheat")
            if rsi > Decimal(os.getenv("RELATIVE_RSI_OVERHEAT", "72")):
                exclusions.append("rsi_overheat")
            if return_15m > 0 and return_1h < 0 and return_4h < 0:
                exclusions.append("only_5m_15m_strength")
            row["relative_score"] = score
            row["raw_relative_score"] = score
            row["relative_score"] = self._smoothed_relative_score(str(row.get("symbol", "")), score)
            row["score_parts"] = score_parts
            row["vwap_position_pct"] = vwap_position
            row["ema20_position_pct"] = ema20_position
            row["return_1h_score"] = return_1h_score
            row["return_4h_score"] = return_4h_score
            row["bid_ask_depth_pressure_score"] = depth_pressure
            row["category_relative_strength_score"] = category_score
            row["funding_overheat_penalty"] = funding_overheat
            row["rsi_overheat_penalty"] = rsi_overheat
            row["spread_penalty"] = spread_penalty
            row["low_liquidity_penalty"] = low_liquidity_penalty
            row["oi_24h_overheat_penalty"] = oi_24h_overheat
            row["price_surge_penalty"] = price_surge_penalty
            row["volume_growth_score_pct"] = cap(volume_1h, Decimal("50"))
            row["exclude_reasons"] = exclusions
            row["eligible"] = not exclusions
            row["score_note"] = "normalized 1h/4h return + volume + OI + EMA - overheat/funding/RSI/VWAP/spread/liquidity"
            scored.append(row)
        scored = sorted(scored, key=lambda item: Decimal(str(item["relative_score"])), reverse=True)
        cutoff = max(1, int(len(scored) * 0.2))
        long_symbols = {item["symbol"] for item in scored[:cutoff]}
        short_symbols = {item["symbol"] for item in scored[-cutoff:]}
        for index, item in enumerate(scored):
            return_1h = Decimal(str(item.get("return_1h_pct") or 0))
            return_4h = Decimal(str(item.get("return_4h_pct") or 0))
            volume_1h = Decimal(str(item.get("volume_change_1h_pct") or 0))
            ema20_position = Decimal(str(item.get("ema20_position_pct") or 0))
            item["rank_percentile"] = (Decimal(str(index)) / Decimal(str(max(1, len(scored) - 1)))) * Decimal("100")
            item["long_candidate"] = (
                item["symbol"] in long_symbols
                and item["eligible"]
                and return_1h > 0
                and return_4h > 0
                and volume_1h > 0
                and ema20_position > 0
            )
            item["short_candidate"] = (
                item["symbol"] in short_symbols
                and item["eligible"]
                and return_1h < 0
                and return_4h < 0
                and ema20_position < 0
            )
        return scored

    def _build_relative_rankings(self) -> dict[str, Any]:
        rows_15m = self._relative_returns(15)
        rows_1h = self._relative_returns(60)
        rows_4h = self._relative_returns(240)
        latest = self.relative_history[-1] if self.relative_history else {}
        row15_by_symbol = {row["symbol"]: row["return_pct"] for row in rows_15m}
        row4_by_symbol = {row["symbol"]: row["return_pct"] for row in rows_4h}
        combined = []
        for row in rows_1h:
            symbol = row["symbol"]
            series = self._relative_series(symbol)
            ema_fast = self._ema(series, 9)
            ema_slow = self._ema(series, 21)
            ema20 = self._ema(series, 20)
            latest_price = series[-1] if series else Decimal("0")
            vwap_1h = self._vwap(symbol, 240)
            price_chart = self._price_return_since_jst_9_chart(symbol)
            candle_chart = self._historical_candles_for_symbol(symbol)
            ema_trend = Decimal("0")
            if ema_fast is not None and ema_slow is not None and ema_slow > 0:
                ema_trend = ((ema_fast - ema_slow) / ema_slow) * Decimal("100")
            combined.append(
                {
                    "symbol": symbol,
                    "return_15m_pct": row15_by_symbol.get(symbol, self._relative_return_pct(symbol, 15)),
                    "return_1h_pct": row["return_pct"],
                    "return_4h_pct": row4_by_symbol.get(symbol, row["return_pct"]),
                    "return_24h_pct": self._historical_return_pct(symbol, 24),
                    "return_since_9jst_pct": self._relative_return_from_jst_9(symbol),
                    "ema_fast": ema_fast,
                    "ema_slow": ema_slow,
                    "ema20": ema20,
                    "ema_trend_pct": ema_trend,
                    "rsi": self._rsi(series),
                    "atr_pct": self._atr_pct(series),
                    "vwap": latest_price,
                    "vwap_1h": vwap_1h,
                    "liquidity_quote": (latest.get("liquidity_quote") or {}).get(symbol, 0),
                    "bid": (latest.get("bids") or {}).get(symbol),
                    "ask": (latest.get("asks") or {}).get(symbol),
                    "spread_pct": (latest.get("spread_pct") or {}).get(symbol),
                    "volume_growth_pct": self._liquidity_growth_pct(symbol),
                    "volume_change_15m_pct": self._liquidity_growth_minutes(symbol, 15),
                    "volume_change_1h_pct": self._liquidity_growth_minutes(symbol, 60),
                    "volume_change_4h_pct": self._liquidity_growth_minutes(symbol, 240),
                    "real_volume_change_15m_pct": self._market_metric_change_minutes(symbol, "ohlcv_volume", 15),
                    "real_volume_change_1h_pct": self._market_metric_change_minutes(symbol, "ohlcv_volume", 60),
                    "real_volume_change_4h_pct": self._market_metric_change_minutes(symbol, "ohlcv_volume", 240),
                    "volume_source": "ohlcv_volume_when_available_else_orderbook_liquidity_proxy",
                    "bid_liquidity_quote": (latest.get("bid_liquidity_quote") or {}).get(symbol, 0),
                    "ask_liquidity_quote": (latest.get("ask_liquidity_quote") or {}).get(symbol, 0),
                    "bid_depth_change_1h_pct": self._depth_growth_minutes(symbol, "bid_liquidity_quote", 60),
                    "ask_depth_change_1h_pct": self._depth_growth_minutes(symbol, "ask_liquidity_quote", 60),
                    "bid_depth_change_4h_pct": self._depth_growth_minutes(symbol, "bid_liquidity_quote", 240),
                    "ask_depth_change_4h_pct": self._depth_growth_minutes(symbol, "ask_liquidity_quote", 240),
                    "volume_since_9jst": self._liquidity_since_jst_9(symbol),
                    "price_return_since_9jst_series": price_chart["values"],
                    "price_return_since_9jst_times": price_chart["times"],
                    "price_candles": candle_chart["candles"],
                    "price_candle_timeframe": candle_chart["timeframe"],
                    "price_candle_days": candle_chart["days"],
                    "price_candle_exchange": candle_chart["exchange"],
                    "open_interest": self._latest_market_average(symbol, "open_interest"),
                    "oi_change_15m_pct": self._market_metric_change_minutes(symbol, "open_interest", 15),
                    "oi_change_1h_pct": self._market_metric_change_minutes(symbol, "open_interest", 60),
                    "oi_change_4h_pct": self._market_metric_change_minutes(symbol, "open_interest", 240),
                    "oi_change_24h_pct": self._market_metric_change_minutes(symbol, "open_interest", 1440),
                    "funding_rate": self._latest_market_average(symbol, "funding_rate"),
                    "liquidation": None,
                    "data_status": "live mid/orderbook; OHLCV/OI/funding recorded when available; liquidation pending",
                }
            )
        combined = self._score_relative_features(combined)
        strong = combined[:12]
        weak = list(reversed(combined[-12:]))
        long_candidates = [item for item in combined if item.get("long_candidate")]
        short_candidates = [item for item in reversed(combined) if item.get("short_candidate")]
        visible_symbols = {item["symbol"] for item in strong + weak}
        features = []
        for item in combined:
            if item["symbol"] in visible_symbols:
                features.append(item)
            else:
                slim = dict(item)
                slim.pop("price_return_since_9jst_series", None)
                slim.pop("price_return_since_9jst_times", None)
                slim.pop("price_candles", None)
                slim.pop("volume_since_9jst", None)
                features.append(slim)
        return to_jsonable(
            {
                "updated_at": datetime.now(timezone.utc),
                "strong": strong,
                "weak": weak,
                "long_candidates": long_candidates,
                "short_candidates": short_candidates,
                "features": features,
            }
        )

    def _select_relative_short_basket(self, long_symbol: str, count: int = 4) -> list[dict[str, Any]]:
        weak = self.relative_rankings.get("weak", [])
        basket = [item for item in weak if item.get("symbol") != long_symbol]
        return basket[:count]

    async def open_relative_position_async(self, request: RelativeTradeRequest) -> dict[str, Any]:
        latest_mids = dict(self.relative_history[-1]["mids"]) if self.relative_history else {}
        symbols = [request.symbol.strip().upper()] + [symbol.strip().upper() for symbol in request.short_symbols]
        symbols = [symbol if "/" in symbol else f"{symbol}/USDT" for symbol in symbols if symbol]
        missing = [symbol for symbol in symbols if symbol not in latest_mids]
        for symbol in missing:
            mid = await self._fetch_relative_mid(symbol)
            if mid is not None:
                latest_mids[symbol] = mid
        return self.open_relative_position(request, latest_mids=latest_mids)

    async def _fetch_relative_mid(self, symbol: str) -> Decimal | None:
        exchange_ids = [item.lower() for item in parse_csv(self.settings.futures_exchanges)]
        mids = []
        for exchange_id in exchange_ids:
            try:
                book = await self._fetch_direct_futures_orderbook(exchange_id, symbol, max(5, int(self.settings.orderbook_limit)))
                bids = book.get("bids") or []
                asks = book.get("asks") or []
                if not bids or not asks:
                    continue
                bid = Decimal(str(bids[0][0]))
                ask = Decimal(str(asks[0][0]))
                if bid > 0 and ask > 0:
                    mids.append((bid + ask) / Decimal("2"))
            except Exception:
                continue
        if not mids:
            return None
        return sum(mids) / Decimal(str(len(mids)))

    def open_relative_position(self, request: RelativeTradeRequest, latest_mids: dict[str, Any] | None = None) -> dict[str, Any]:
        rankings = self._build_relative_rankings()
        self.relative_rankings = rankings
        strong = rankings.get("strong", [])
        long_symbol = request.symbol.strip().upper()
        if request.mode == "auto" or not long_symbol:
            if not strong:
                raise HTTPException(status_code=400, detail="relative ranking is not ready yet")
            long_symbol = strong[0]["symbol"]
        if "/" not in long_symbol:
            long_symbol = f"{long_symbol}/USDT"
        latest_mids = latest_mids or (self.relative_history[-1]["mids"] if self.relative_history else {})
        if long_symbol not in latest_mids:
            raise HTTPException(status_code=400, detail=f"{long_symbol} price is not ready")
        requested_shorts = [symbol.strip().upper() for symbol in request.short_symbols if symbol.strip()]
        requested_shorts = [symbol if "/" in symbol else f"{symbol}/USDT" for symbol in requested_shorts]
        if requested_shorts:
            short_symbols = [symbol for symbol in requested_shorts if symbol != long_symbol and symbol in latest_mids]
        else:
            basket = self._select_relative_short_basket(long_symbol)
            if not basket:
                raise HTTPException(status_code=400, detail="short basket is not ready yet")
            short_symbols = [item["symbol"] for item in basket if item["symbol"] in latest_mids]
        if not short_symbols:
            raise HTTPException(status_code=400, detail="short basket prices are not ready")
        key = f"{long_symbol}|{','.join(short_symbols)}"
        now = datetime.now(timezone.utc)
        quote_amount = Decimal(str(request.quote_amount))
        long_vol = self._relative_sizing_volatility_pct(long_symbol)
        short_vols = {symbol: self._relative_sizing_volatility_pct(symbol) for symbol in short_symbols}
        hedge_mode = os.getenv("RELATIVE_HEDGE_MODE", "volatility_adjusted").strip().lower()
        long_quote_amount = quote_amount
        if hedge_mode in {"vol", "volatility", "volatility_adjusted", "risk"}:
            short_quote_amounts = {
                symbol: (quote_amount / Decimal(str(len(short_symbols)))) * (long_vol / max(short_vol, Decimal("0.0001")))
                for symbol, short_vol in short_vols.items()
            }
            hedge_basis = "volatility_adjusted_notional"
        else:
            short_quote_amounts = {symbol: quote_amount / Decimal(str(len(short_symbols))) for symbol in short_symbols}
            hedge_basis = "equal_total_notional"
        position = {
            "id": key,
            "mode": request.mode,
            "long_symbol": long_symbol,
            "short_symbols": short_symbols,
            "quote_amount": quote_amount,
            "long_quote_amount": long_quote_amount,
            "short_quote_amounts": short_quote_amounts,
            "short_total_quote_amount": sum(short_quote_amounts.values()),
            "hedge_basis": hedge_basis,
            "sizing_long_vol_pct": long_vol,
            "sizing_short_vols_pct": short_vols,
            "opened_at": now,
            "entry_long_price": Decimal(str(latest_mids[long_symbol])),
            "entry_short_prices": {symbol: Decimal(str(latest_mids[symbol])) for symbol in short_symbols},
            "last_relative_pct": Decimal("0"),
            "unrealized_profit": Decimal("0"),
        }
        self.relative_positions[key] = position
        self.log("relative", f"REL PAPER entry long {long_symbol} / short {', '.join(short_symbols)}")
        self._refresh_relative_pnl()
        return to_jsonable(position)

    def close_relative_position(self, position_id: str, status: str = "manual_close") -> None:
        position = self.relative_positions.get(position_id)
        if not position:
            raise HTTPException(status_code=404, detail="relative position not found")
        profit = Decimal(str(position.get("unrealized_profit", "0")))
        trade = {
            "timestamp": datetime.now(timezone.utc),
            "mode": "relative_paper",
            "long_symbol": position["long_symbol"],
            "short_symbols": position["short_symbols"],
            "quote_amount": position["quote_amount"],
            "long_quote_amount": position.get("long_quote_amount", position["quote_amount"]),
            "short_quote_amounts": position.get("short_quote_amounts", {}),
            "short_total_quote_amount": position.get("short_total_quote_amount"),
            "hedge_basis": position.get("hedge_basis", ""),
            "relative_pct": position.get("last_relative_pct", Decimal("0")),
            "profit_quote": profit,
            "status": status,
        }
        self.relative_closed_trades.appendleft(to_jsonable(trade))
        append_jsonl(TRADE_LOG_PATH, trade)
        self.relative_realized_profit += profit
        self.relative_positions.pop(position_id, None)
        self._refresh_relative_pnl(close_on_threshold=False)
        self.log("relative", f"REL PAPER {status} {position['long_symbol']}: pnl {profit:.4f}")

    def _update_relative_auto_strategy(self) -> None:
        if len(self.relative_history) < 2:
            return
        strong = self.relative_rankings.get("strong", [])
        weak = self.relative_rankings.get("weak", [])
        basket_size = int(os.getenv("RELATIVE_AUTO_BASKET_SIZE", "5"))
        auto_positions = [position for position in self.relative_positions.values() if position.get("mode") == "auto_top_bottom"]
        if len(auto_positions) >= basket_size:
            return
        existing_auto_longs = {position.get("long_symbol") for position in auto_positions}
        if len(strong) < basket_size or len(weak) < basket_size:
            return
        latest_mids = self.relative_history[-1]["mids"] if self.relative_history else {}
        long_symbols = [item["symbol"] for item in strong[:basket_size] if item.get("symbol") in latest_mids]
        short_symbols = [item["symbol"] for item in weak[:basket_size] if item.get("symbol") in latest_mids]
        if len(long_symbols) < basket_size or len(short_symbols) < basket_size:
            return
        total_quote = Decimal(os.getenv("RELATIVE_PAPER_QUOTE", "10"))
        per_long_quote = total_quote / Decimal(str(basket_size))
        for long_symbol in long_symbols:
            if len([position for position in self.relative_positions.values() if position.get("mode") == "auto_top_bottom"]) >= basket_size:
                break
            if long_symbol in short_symbols:
                continue
            if long_symbol in existing_auto_longs:
                continue
            key = f"{long_symbol}|{','.join(short_symbols)}"
            if key in self.relative_positions:
                continue
            self.open_relative_position(
                RelativeTradeRequest(
                    symbol=long_symbol,
                    short_symbols=short_symbols,
                    mode="auto_top_bottom",
                    quote_amount=float(per_long_quote),
                ),
                latest_mids=latest_mids,
            )

    def _refresh_relative_pnl(self, close_on_threshold: bool = True) -> None:
        latest_mids = self.relative_history[-1]["mids"] if self.relative_history else {}
        unrealized = Decimal("0")
        take_profit = Decimal(os.getenv("RELATIVE_TAKE_PROFIT_PCT", "5.0"))
        stop_loss = Decimal(os.getenv("RELATIVE_STOP_LOSS_PCT", "-1.5"))
        min_hold_minutes = Decimal(os.getenv("RELATIVE_MIN_HOLD_MINUTES", "30"))
        now_time = datetime.now(timezone.utc)
        to_close: list[tuple[str, str]] = []
        for position_id, position in self.relative_positions.items():
            long_symbol = position["long_symbol"]
            if long_symbol not in latest_mids:
                continue
            long_entry = Decimal(str(position["entry_long_price"]))
            long_now = Decimal(str(latest_mids[long_symbol]))
            long_ret = ((long_now - long_entry) / long_entry) * Decimal("100")
            short_returns = []
            short_pnl = Decimal("0")
            for symbol, entry_price in position["entry_short_prices"].items():
                if symbol not in latest_mids:
                    continue
                entry = Decimal(str(entry_price))
                now = Decimal(str(latest_mids[symbol]))
                short_ret = ((now - entry) / entry) * Decimal("100")
                short_returns.append(short_ret)
                short_amount = Decimal(str((position.get("short_quote_amounts") or {}).get(symbol, Decimal("0"))))
                short_pnl += short_amount * ((Decimal("0") - short_ret) / Decimal("100"))
            if not short_returns:
                continue
            short_avg = sum(short_returns) / Decimal(str(len(short_returns)))
            long_amount = Decimal(str(position.get("long_quote_amount", position["quote_amount"])))
            long_pnl = long_amount * (long_ret / Decimal("100"))
            total_capital = long_amount + sum(Decimal(str(value)) for value in (position.get("short_quote_amounts") or {}).values())
            raw_profit = long_pnl + short_pnl
            relative_pct = (raw_profit / total_capital) * Decimal("100") if total_capital > 0 else long_ret - short_avg
            if os.getenv("RELATIVE_VOL_ADJUST", "true").strip().lower() in {"1", "true", "yes", "on"}:
                long_vol = self._relative_volatility_pct(long_symbol)
                short_vols = [self._relative_volatility_pct(symbol) for symbol in position["entry_short_prices"]]
                short_vol = sum(short_vols) / Decimal(str(len(short_vols))) if short_vols else Decimal("1")
                display_relative_pct = (long_ret / long_vol) - (short_avg / short_vol)
                position["relative_basis"] = "vol_adjusted"
                position["display_relative_pct"] = display_relative_pct
                position["long_return_pct"] = long_ret
                position["short_return_pct"] = short_avg
                position["long_vol_pct"] = long_vol
                position["short_vol_pct"] = short_vol
            else:
                position["relative_basis"] = "raw"
                position["display_relative_pct"] = relative_pct
                position["long_return_pct"] = long_ret
                position["short_return_pct"] = short_avg
            profit = raw_profit
            opened_at = position.get("opened_at", now_time)
            if isinstance(opened_at, str):
                opened_at = datetime.fromisoformat(opened_at)
            held_minutes = Decimal(str((now_time - opened_at).total_seconds() / 60))
            position["last_relative_pct"] = relative_pct
            position["long_unrealized_profit"] = long_pnl
            position["short_unrealized_profit"] = short_pnl
            position["gross_relative_capital"] = total_capital
            position["unrealized_profit"] = profit
            position["held_minutes"] = held_minutes
            position["min_hold_minutes"] = min_hold_minutes
            unrealized += profit
            can_auto_close = close_on_threshold and held_minutes >= min_hold_minutes
            if can_auto_close and relative_pct >= take_profit:
                to_close.append((position_id, "take_profit"))
            elif can_auto_close and relative_pct <= stop_loss:
                to_close.append((position_id, "stop_loss"))
        self.relative_unrealized_profit = unrealized
        for position_id, status in to_close:
            self.close_relative_position(position_id, status=status)

    def _update_futures_paper_strategy(self, points: list[dict[str, Any]]) -> None:
        entry_threshold = Decimal(os.getenv("FUTURES_PAPER_ENTRY_SPREAD_PCT", "0.05"))
        max_expected_spread = Decimal(os.getenv("FUTURES_MAX_EXPECTED_SPREAD_PCT", "4.0"))
        add_thresholds = [
            Decimal(os.getenv("FUTURES_PAPER_ADD_SPREAD_PCT", "1.0")),
            Decimal(os.getenv("FUTURES_PAPER_SECOND_ADD_SPREAD_PCT", "1.5")),
            Decimal(os.getenv("FUTURES_PAPER_THIRD_ADD_SPREAD_PCT", "2.0")),
            Decimal(os.getenv("FUTURES_PAPER_FOURTH_ADD_SPREAD_PCT", "3.0")),
            max_expected_spread,
        ]
        take_profit_threshold = Decimal(os.getenv("FUTURES_EXIT_SPREAD_PCT", "0.0"))
        compromise_minutes = Decimal(os.getenv("FUTURES_COMPROMISE_MINUTES", "30"))
        compromise_threshold = Decimal(os.getenv("FUTURES_COMPROMISE_EXIT_SPREAD_PCT", "0.05"))
        quote_amount = Decimal(os.getenv("FUTURES_PAPER_QUOTE", "10"))
        now = datetime.now(timezone.utc)

        def record_paper_event(action: str, symbol: str, point: dict[str, Any], payload: dict[str, Any]) -> None:
            append_jsonl(
                FUTURES_PAPER_DEMO_LOG_PATH,
                {
                    "timestamp": now,
                    "mode": "futures_paper_demo",
                    "action": action,
                    "symbol": symbol,
                    "direction": point.get("direction", ""),
                    "entry_threshold_pct": entry_threshold,
                    "spread_pct": point.get("spread_pct"),
                    "executable_spread_pct": point.get("executable_spread_pct"),
                    "net_spread_pct": point.get("net_spread_pct"),
                    "round_trip_cost_pct": point.get("round_trip_cost_pct"),
                    "capacity_quote": point.get("capacity_quote"),
                    **payload,
                },
            )

        for point in points:
            symbol = point["symbol"]
            if not point.get("is_executable"):
                continue
            spread = Decimal(str(point.get("net_spread_pct") or point["spread_pct"]))
            position = self.futures_positions.get(symbol)

            if position is None:
                if spread >= entry_threshold:
                    self.futures_positions[symbol] = {
                        "symbol": symbol,
                        "direction": point.get("direction", ""),
                        "entry_spread_pct": spread,
                        "quote_amount": quote_amount,
                        "opened_at": now,
                        "add_count": 0,
                        "last_spread_pct": spread,
                        "max_spread_pct": spread,
                        "max_expected_spread_pct": max_expected_spread,
                        "risk_mode": "no_stop_wait_reversion",
                    }
                    record_paper_event(
                        "entry",
                        symbol,
                        point,
                        {
                            "quote_amount": quote_amount,
                            "avg_entry_spread_pct": spread,
                            "add_count": 0,
                            "unrealized_profit": Decimal("0"),
                        },
                    )
                    self.log("paper", f"FUTURES PAPER entry {symbol}: {spread:.4f}% {point.get('direction', '')}")
                continue

            position["last_spread_pct"] = spread
            position["max_spread_pct"] = max(Decimal(str(position.get("max_spread_pct", spread))), spread)
            if spread > max_expected_spread:
                position["above_expected_max"] = True
            held_minutes = Decimal(str((now - position["opened_at"]).total_seconds() / 60))
            next_add_threshold = None
            add_count = int(position["add_count"])
            if add_count < len(add_thresholds):
                next_add_threshold = add_thresholds[add_count]

            if next_add_threshold is not None and spread >= next_add_threshold:
                old_amount = Decimal(str(position["quote_amount"]))
                old_entry = Decimal(str(position["entry_spread_pct"]))
                new_amount = old_amount + quote_amount
                position["entry_spread_pct"] = ((old_entry * old_amount) + (spread * quote_amount)) / new_amount
                position["quote_amount"] = new_amount
                position["add_count"] += 1
                self.log(
                    "paper",
                    f"FUTURES PAPER add{position['add_count']} {symbol}: {spread:.4f}% avg {position['entry_spread_pct']:.4f}%",
                )
                record_paper_event(
                    "add",
                    symbol,
                    point,
                    {
                        "quote_amount": position["quote_amount"],
                        "avg_entry_spread_pct": position["entry_spread_pct"],
                        "add_count": position["add_count"],
                        "unrealized_profit": Decimal(str(position["quote_amount"]))
                        * ((Decimal(str(position["entry_spread_pct"])) - spread) / Decimal("100")),
                    },
                )

            should_take_profit = spread <= take_profit_threshold
            should_compromise = held_minutes >= compromise_minutes and spread <= compromise_threshold
            if not (should_take_profit or should_compromise):
                continue

            entry = Decimal(str(position["entry_spread_pct"]))
            amount = Decimal(str(position["quote_amount"]))
            profit = amount * ((entry - spread) / Decimal("100"))
            trade = {
                "timestamp": now,
                "symbol": symbol,
                "buy_exchange": "",
                "sell_exchange": "",
                "buy_price": Decimal("0"),
                "sell_price": Decimal("0"),
                "base_amount": Decimal("0"),
                "quote_amount": amount,
                "net_profit_pct": entry - spread,
                "profit_quote": profit,
                "mode": "futures_paper",
                "status": "take_profit" if should_take_profit else "compromise_exit",
                "entry_spread_pct": entry,
                "max_spread_pct": position.get("max_spread_pct", spread),
                "exit_spread_pct": spread,
                "held_minutes": held_minutes,
                "add_count": position["add_count"],
                "direction": position.get("direction", ""),
                "risk_mode": position.get("risk_mode", ""),
            }
            json_trade = to_jsonable(trade)
            self.futures_closed_trades.appendleft(json_trade)
            self.demo.trades.appendleft(json_trade)
            append_jsonl(TRADE_LOG_PATH, trade)
            record_paper_event(
                "exit",
                symbol,
                point,
                {
                    "quote_amount": amount,
                    "avg_entry_spread_pct": entry,
                    "exit_spread_pct": spread,
                    "net_profit_pct": entry - spread,
                    "profit_quote": profit,
                    "held_minutes": held_minutes,
                    "add_count": position["add_count"],
                    "status": trade["status"],
                },
            )
            self.demo.realized_profit += profit
            self.demo.cash += profit
            self.log("paper", f"FUTURES PAPER exit {symbol}: entry {entry:.4f}% exit {spread:.4f}% pnl {profit:.4f}")
            self.futures_positions.pop(symbol, None)

        self._refresh_futures_paper_pnl()

    def _refresh_futures_paper_pnl(self) -> None:
        unrealized = Decimal("0")
        for position in self.futures_positions.values():
            entry = Decimal(str(position["entry_spread_pct"]))
            last = Decimal(str(position["last_spread_pct"]))
            amount = Decimal(str(position["quote_amount"]))
            unrealized += amount * ((entry - last) / Decimal("100"))
        realized = Decimal("0")
        for trade in self.futures_closed_trades:
            realized += Decimal(str(trade.get("profit_quote", "0") or "0"))
        self.futures_unrealized_profit = unrealized
        self.futures_realized_profit = realized

    async def _refresh_balances(self, exchanges) -> None:
        balances = []
        for exchange in exchanges:
            try:
                balance = await exchange.fetch_balance()
                totals = {
                    asset: amount
                    for asset, amount in (balance.get("total") or {}).items()
                    if amount and Decimal(str(amount)) != 0
                }
                balances.append({"exchange_id": exchange.id, "status": "ok", "total": totals})
            except Exception as exc:
                balances.append({"exchange_id": exchange.id, "status": "error", "message": f"{type(exc).__name__}: {exc}"})
        self.balances = to_jsonable(balances)

    def _upsert_market_status(self, status: dict[str, Any]) -> None:
        items = [
            item
            for item in self.market_statuses
            if not (item.get("exchange_id") == status["exchange_id"] and item.get("symbol") == status["symbol"])
        ]
        items.append(to_jsonable(status))
        self.market_statuses = sorted(items, key=lambda item: (item["exchange_id"], item["symbol"]))

    def state(self) -> dict[str, Any]:
        running = bool(self.task and not self.task.done())
        self._refresh_futures_paper_pnl()
        futures_start_cash = Decimal(os.getenv("FUTURES_PAPER_START_CASH", "10000"))
        relative_start_cash = Decimal(os.getenv("RELATIVE_PAPER_START_CASH", "10000"))
        futures_total = self.futures_realized_profit + self.futures_unrealized_profit
        relative_total = self.relative_realized_profit + self.relative_unrealized_profit
        relative_rankings = self._state_relative_rankings()
        return {
            "running": running,
            "settings": self.settings.model_dump(),
            "quotes": self.quotes,
            "market_statuses": self.market_statuses,
            "opportunities": self.opportunities,
            "spread_history": list(self.spread_history)[-20:],
            "futures_spread_history": list(self.futures_spread_history)[-3:],
            "futures_market_statuses": self._state_market_statuses(),
            "logs": list(self.logs),
            "last_error": self.last_error,
            "last_tick": self.last_tick,
            "stopped_at": self.stopped_at,
            "portfolio": self.demo.portfolio(),
            "trades": list(self.demo.trades),
            "futures_positions": to_jsonable(list(self.futures_positions.values())),
            "futures_closed_trades": list(self.futures_closed_trades),
            "futures_paper_demo_events": list(reversed(read_tail_jsonl(FUTURES_PAPER_DEMO_LOG_PATH, 100))),
            "futures_paper_pnl": to_jsonable(
                {
                    "realized": self.futures_realized_profit,
                    "unrealized": self.futures_unrealized_profit,
                    "total": futures_total,
                }
            ),
            "futures_paper_account": to_jsonable(
                {
                    "starting_cash": futures_start_cash,
                    "equity": futures_start_cash + futures_total,
                    "realized": self.futures_realized_profit,
                    "unrealized": self.futures_unrealized_profit,
                    "trade_count": len(self.futures_closed_trades),
                    "open_count": len(self.futures_positions),
                }
            ),
            "futures_perf": to_jsonable(self.futures_perf),
            "futures_base_symbols": self.futures_base_symbols,
            "futures_active_symbols": self.futures_active_symbols,
            "futures_boost_symbols": sorted(self.futures_boost_symbols.keys()),
            "futures_movement_symbols": to_jsonable(self.futures_movement_symbols),
            "relative_rankings": relative_rankings,
            "relative_feature_history_count": len(self.relative_feature_history),
            "relative_positions": to_jsonable(list(self.relative_positions.values())),
            "relative_closed_trades": list(self.relative_closed_trades),
            "relative_pnl": to_jsonable(
                {
                    "realized": self.relative_realized_profit,
                    "unrealized": self.relative_unrealized_profit,
                    "total": relative_total,
                }
            ),
            "relative_paper_account": to_jsonable(
                {
                    "starting_cash": relative_start_cash,
                    "equity": relative_start_cash + relative_total,
                    "realized": self.relative_realized_profit,
                    "unrealized": self.relative_unrealized_profit,
                    "trade_count": len(self.relative_closed_trades),
                    "open_count": len(self.relative_positions),
                }
            ),
            "historical_candle_status": to_jsonable(self.historical_candle_status),
            "balances": self.balances,
            "preflight_results": self.preflight_results,
            "live_ready": os.getenv("LIVE_TRADING", "false").strip().lower() == "true",
            "live_confirm_text": LIVE_CONFIRM_TEXT,
        }

    def _state_relative_rankings(self) -> dict[str, Any]:
        def slim_row(row: dict[str, Any]) -> dict[str, Any]:
            item = dict(row)
            item.pop("price_candles", None)
            item.pop("volume_since_9jst", None)
            item.pop("price_return_since_9jst_series", None)
            item.pop("price_return_since_9jst_times", None)
            return item

        rankings = self.relative_rankings or {}
        return {
            "updated_at": rankings.get("updated_at"),
            "strong": [slim_row(row) for row in rankings.get("strong", [])],
            "weak": [slim_row(row) for row in rankings.get("weak", [])],
            "long_candidates": [slim_row(row) for row in rankings.get("long_candidates", [])],
            "short_candidates": [slim_row(row) for row in rankings.get("short_candidates", [])],
            "features": [slim_row(row) for row in rankings.get("features", [])],
        }

    def _state_market_statuses(self) -> list[dict[str, Any]]:
        keys = {
            "exchange_id",
            "symbol",
            "futures_symbol",
            "status",
            "message",
            "bid",
            "ask",
            "mid",
            "bid_capacity_quote",
            "ask_capacity_quote",
            "taker_fee_pct",
            "fee_source",
            "timestamp",
        }
        return [{key: item.get(key) for key in keys if key in item} for item in self.futures_market_statuses[-240:]]


app = FastAPI()
runtime = BotRuntime()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def get_state():
    return runtime.state()


@app.get("/api/relative/chart/{symbol:path}")
async def get_relative_chart(symbol: str):
    normalized = symbol.strip().upper()
    if "/" not in normalized:
        normalized = f"{normalized}/USDT"
    return to_jsonable(runtime._historical_candles_for_symbol(normalized))


@app.get("/api/history")
async def get_history(limit: int = 200):
    limit = max(1, min(limit, 1000))
    return {
        "app_log": read_tail_lines(APP_LOG_PATH, limit),
        "trades": list(reversed(read_tail_jsonl(TRADE_LOG_PATH, limit))),
        "spread_history": list(reversed(read_tail_jsonl(SPREAD_LOG_PATH, limit))),
        "futures_spread_history": list(reversed(read_tail_jsonl(FUTURES_SPREAD_LOG_PATH, limit))),
        "futures_paper_demo": list(reversed(read_tail_jsonl(FUTURES_PAPER_DEMO_LOG_PATH, limit))),
        "relative_features": list(reversed(read_tail_jsonl(RELATIVE_FEATURE_LOG_PATH, limit))),
        "relative_market_data": list(reversed(read_tail_jsonl(RELATIVE_MARKET_DATA_LOG_PATH, limit))),
        "futures_event_report": futures_event_report(limit=limit),
        "files": {
            "app_log": str(APP_LOG_PATH),
            "trades": str(TRADE_LOG_PATH),
            "spread_history": str(SPREAD_LOG_PATH),
            "futures_spread_history": str(FUTURES_SPREAD_LOG_PATH),
            "futures_paper_demo": str(FUTURES_PAPER_DEMO_LOG_PATH),
            "relative_features": str(RELATIVE_FEATURE_LOG_PATH),
            "relative_market_data": str(RELATIVE_MARKET_DATA_LOG_PATH),
            "historical_candles": str(HISTORICAL_CANDLE_LOG_PATH),
        },
    }


@app.get("/api/relative/learning-report")
async def get_relative_learning_report(limit: int = 500, horizon_minutes: int = 30, assumed_cost_pct: float = 0.10):
    memory_snapshots = list(runtime.relative_feature_history)[-limit:]
    return relative_learning_report(
        limit=max(50, min(limit, 5000)),
        horizon_minutes=max(1, min(horizon_minutes, 240)),
        assumed_cost_pct=Decimal(str(assumed_cost_pct)),
        snapshots=memory_snapshots if memory_snapshots else None,
    )


@app.get("/api/history/app-log.txt")
async def download_app_log():
    if not APP_LOG_PATH.exists():
        raise HTTPException(status_code=404, detail="app.log is not created yet")
    return FileResponse(APP_LOG_PATH, media_type="text/plain", filename="app.log")


@app.post("/api/start")
async def start(settings: BotSettings):
    await runtime.start(settings)
    return runtime.state()


@app.post("/api/settings")
async def update_settings(settings: BotSettings):
    save_settings(settings)
    runtime.settings = settings
    runtime.log("info", "Settings saved")
    return runtime.state()


@app.post("/api/stop")
async def stop():
    await runtime.stop()
    return runtime.state()


@app.post("/api/reset-demo")
async def reset_demo():
    runtime.demo.reset()
    runtime.log("info", "繝・Δ蜿｣蠎ｧ繧偵Μ繧ｻ繝・ヨ縺励∪縺励◆")
    return runtime.state()


@app.post("/api/manual-demo-trade")
async def manual_demo_trade(request: ManualDemoTrade):
    trade = runtime.demo.manual_trade(request)
    runtime.log("fill", f"Manual demo {trade['symbol']}: {trade['profit_quote']} USDT")
    return runtime.state()


@app.post("/api/demo-price-adjustment")
async def demo_price_adjustment(request: DemoPriceAdjustment):
    runtime.set_demo_price_adjustment(request)
    runtime.log(
        "info",
        f"Demo price adjustment {request.exchange_id} {request.symbol}: bid {request.bid_adjust_pct}%, ask {request.ask_adjust_pct}%",
    )
    return runtime.state()


@app.post("/api/clear-demo-price-adjustments")
async def clear_demo_price_adjustments():
    runtime.clear_demo_price_adjustments()
    runtime.log("info", "Demo price adjustments cleared")
    return runtime.state()


@app.post("/api/preflight")
async def preflight(request: PreflightRequest):
    runtime.preflight_results = await asyncio.to_thread(
        run_preflight_sync,
        request,
        Decimal(str(runtime.settings.default_taker_fee_pct)),
    )
    runtime.log("info", "Preflight check completed")
    return runtime.state()


@app.post("/api/historical-candles/backfill")
async def historical_candles_backfill(request: HistoricalCandlesRequest):
    return await runtime.backfill_historical_candles(request)


@app.post("/api/relative/open")
async def relative_open(request: RelativeTradeRequest):
    await runtime.open_relative_position_async(request)
    return runtime.state()


@app.post("/api/relative/close/{position_id:path}")
async def relative_close(position_id: str):
    runtime.close_relative_position(position_id)
    return runtime.state()

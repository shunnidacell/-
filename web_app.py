from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
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
LIVE_CONFIRM_TEXT = "I_UNDERSTAND_REAL_ORDERS"

load_dotenv(ROOT / ".env")


class BotSettings(BaseModel):
    exchanges: str = Field(default="binance,okx,bitget")
    futures_exchanges: str = Field(default="binance,hyperliquid")
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
        futures_exchanges="binance,hyperliquid",
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
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-limit:]


def read_tail_jsonl(path: Path, limit: int = 200) -> list[dict[str, Any]]:
    rows = []
    for line in read_tail_lines(path, limit):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"raw": line})
    return rows


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
        self.balances: list[dict[str, Any]] = []
        self.exchange_handles: dict[str, Any] = {}
        self.preflight_results: list[dict[str, Any]] = []
        self.demo_price_adjustments: dict[tuple[str, str], dict[str, Decimal]] = {}
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
                config = Config(
                    exchanges=config.exchanges,
                    symbols=common_symbols,
                    trade_size_quote=config.trade_size_quote,
                    min_net_profit_pct=config.min_net_profit_pct,
                    default_taker_fee_pct=config.default_taker_fee_pct,
                    slippage_pct=config.slippage_pct,
                    poll_seconds=config.poll_seconds,
                    orderbook_limit=config.orderbook_limit,
                    mode=config.mode,
                    live_trading=config.live_trading,
                    live_confirm=config.live_confirm,
                )
                self.settings.symbols = ",".join(common_symbols)
                save_settings(self.settings)
                self.log("ready", f"Common futures symbols: {len(common_symbols)}")
            self.log("ready", f"FUTURES research {', '.join(config.symbols)}: {', '.join(futures_exchanges)}")

            while self.stop_event and not self.stop_event.is_set():
                await self._record_futures_spread_history(futures_exchanges, config)
                latest = self.futures_spread_history[-1] if self.futures_spread_history else {"points": []}
                self.quotes = []
                self.market_statuses = self.futures_market_statuses
                self.opportunities = []
                self.last_tick = datetime.now().astimezone().isoformat()

                if latest.get("points"):
                    best = latest["points"][0]
                    self.log(
                        "futures",
                        f"{best['symbol']} {best['direction']} spread {Decimal(str(best['spread_pct'])):.4f}%",
                    )
                else:
                    self.log("futures", "No futures spread data")

                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=config.poll_seconds)
                except asyncio.TimeoutError:
                    pass
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.log("error", self.last_error)
        finally:
            self.stopped_at = datetime.now().astimezone().isoformat()
            self.log("info", "Futures research stopped")

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
                return {
                    item.get("baseCcy", "").upper()
                    for item in data.get("data", [])
                    if item.get("settleCcy") == "USDT" and item.get("state") == "live"
                }
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

        by_symbol: dict[str, list[dict[str, Any]]] = {}
        for quote in quotes:
            by_symbol.setdefault(quote["symbol"], []).append(quote)

        points = []
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
            points.append(
                {
                    "symbol": symbol,
                    "long_exchange": low["exchange_id"],
                    "short_exchange": high["exchange_id"],
                    "low_mid": low["mid"],
                    "high_mid": high["mid"],
                    "spread_pct": spread_pct,
                    "direction": f"long {low['exchange_id']} / short {high['exchange_id']}",
                }
            )

        item = to_jsonable(
            {
                "timestamp": datetime.now(timezone.utc),
                "points": sorted(points, key=lambda point: Decimal(str(point["spread_pct"])), reverse=True),
            }
        )
        self.futures_spread_history.append(item)
        append_jsonl(FUTURES_SPREAD_LOG_PATH, item)

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
        return {
            "running": running,
            "settings": self.settings.model_dump(),
            "quotes": self.quotes,
            "market_statuses": self.market_statuses,
            "opportunities": self.opportunities,
            "spread_history": list(self.spread_history),
            "futures_spread_history": list(self.futures_spread_history),
            "futures_market_statuses": self.futures_market_statuses,
            "logs": list(self.logs),
            "last_error": self.last_error,
            "last_tick": self.last_tick,
            "stopped_at": self.stopped_at,
            "portfolio": self.demo.portfolio(),
            "trades": list(self.demo.trades),
            "balances": self.balances,
            "preflight_results": self.preflight_results,
            "live_ready": os.getenv("LIVE_TRADING", "false").strip().lower() == "true",
            "live_confirm_text": LIVE_CONFIRM_TEXT,
        }


app = FastAPI()
runtime = BotRuntime()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/state")
async def get_state():
    return runtime.state()


@app.get("/api/history")
async def get_history(limit: int = 200):
    limit = max(1, min(limit, 1000))
    return {
        "app_log": read_tail_lines(APP_LOG_PATH, limit),
        "trades": list(reversed(read_tail_jsonl(TRADE_LOG_PATH, limit))),
        "spread_history": list(reversed(read_tail_jsonl(SPREAD_LOG_PATH, limit))),
        "futures_spread_history": list(reversed(read_tail_jsonl(FUTURES_SPREAD_LOG_PATH, limit))),
        "files": {
            "app_log": str(APP_LOG_PATH),
            "trades": str(TRADE_LOG_PATH),
            "spread_history": str(SPREAD_LOG_PATH),
            "futures_spread_history": str(FUTURES_SPREAD_LOG_PATH),
        },
    }


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

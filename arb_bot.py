from __future__ import annotations

import asyncio
import os
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable

import aiohttp
import ccxt as ccxt_sync
import ccxt.async_support as ccxt
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    exchanges: list[str]
    symbols: list[str]
    trade_size_quote: Decimal
    min_net_profit_pct: Decimal
    default_taker_fee_pct: Decimal
    slippage_pct: Decimal
    poll_seconds: float
    orderbook_limit: int
    mode: str
    live_trading: bool
    live_confirm: str

    @property
    def symbol(self) -> str:
        return self.symbols[0]


@dataclass(frozen=True)
class Quote:
    exchange_id: str
    symbol: str
    bid: Decimal
    ask: Decimal
    bid_volume: Decimal
    ask_volume: Decimal
    taker_fee_pct: Decimal
    timestamp: datetime | None


@dataclass(frozen=True)
class Opportunity:
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: Decimal
    sell_price: Decimal
    base_amount: Decimal
    quote_amount: Decimal
    gross_profit_pct: Decimal
    net_profit_pct: Decimal
    estimated_profit_quote: Decimal


@dataclass(frozen=True)
class SimulatedTrade:
    timestamp: datetime
    symbol: str
    buy_exchange: str
    sell_exchange: str
    buy_price: Decimal
    sell_price: Decimal
    base_amount: Decimal
    quote_amount: Decimal
    net_profit_pct: Decimal
    profit_quote: Decimal
    mode: str
    status: str


def decimal_env(name: str, default: str) -> Decimal:
    value = os.getenv(name, default).strip()
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal number, got {value!r}") from exc


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def load_config() -> Config:
    load_dotenv()
    exchange_ids = [item.lower() for item in parse_csv(os.getenv("EXCHANGES", "binance,okx,bitget"))]
    symbols = [item.upper() for item in parse_csv(os.getenv("SYMBOLS", os.getenv("SYMBOL", "BTC/USDT")))]
    if len(exchange_ids) < 2:
        raise ValueError("EXCHANGES must contain at least two exchange ids")
    if not symbols:
        raise ValueError("SYMBOLS must contain at least one symbol")

    return Config(
        exchanges=exchange_ids,
        symbols=symbols,
        trade_size_quote=decimal_env("TRADE_SIZE_QUOTE", "100"),
        min_net_profit_pct=decimal_env("MIN_NET_PROFIT_PCT", "0.20"),
        default_taker_fee_pct=decimal_env("DEFAULT_TAKER_FEE_PCT", "0.10"),
        slippage_pct=decimal_env("SLIPPAGE_PCT", "0.03"),
        poll_seconds=float(os.getenv("POLL_SECONDS", "5")),
        orderbook_limit=int(os.getenv("ORDERBOOK_LIMIT", "10")),
        mode=os.getenv("MODE", "demo").strip().lower(),
        live_trading=os.getenv("LIVE_TRADING", "false").strip().lower() == "true",
        live_confirm=os.getenv("LIVE_CONFIRM", "").strip(),
    )


def make_exchange(exchange_id: str, private: bool = False):
    exchange_class = getattr(ccxt, exchange_id, None)
    if exchange_class is None:
        raise ValueError(f"Unknown ccxt exchange id: {exchange_id}")

    options = {
        "enableRateLimit": True,
        "timeout": 10000,
        "options": {"defaultType": "spot"},
    }
    if private:
        prefix = exchange_id.upper()
        api_key = os.getenv(f"{prefix}_API_KEY")
        secret = os.getenv(f"{prefix}_SECRET")
        password = os.getenv(f"{prefix}_PASSWORD")
        if api_key:
            options["apiKey"] = api_key
        if secret:
            options["secret"] = secret
        if password:
            options["password"] = password

    return exchange_class(options)


def make_sync_exchange(exchange_id: str, private: bool = False):
    exchange_class = getattr(ccxt_sync, exchange_id, None)
    if exchange_class is None:
        raise ValueError(f"Unknown ccxt exchange id: {exchange_id}")

    options = {
        "enableRateLimit": True,
        "timeout": 10000,
        "options": {"defaultType": "spot"},
    }
    if private:
        prefix = exchange_id.upper()
        api_key = os.getenv(f"{prefix}_API_KEY")
        secret = os.getenv(f"{prefix}_SECRET")
        password = os.getenv(f"{prefix}_PASSWORD")
        if api_key:
            options["apiKey"] = api_key
        if secret:
            options["secret"] = secret
        if password:
            options["password"] = password

    return exchange_class(options)


async def load_markets_and_fees(exchange, symbols: Iterable[str], fallback_fee_pct: Decimal) -> dict[str, Decimal]:
    markets = await exchange.load_markets()
    fees: dict[str, Decimal] = {}
    for symbol in symbols:
        market = markets.get(symbol)
        if not market:
            continue
        taker = market.get("taker")
        fees[symbol] = Decimal(str(taker)) * Decimal("100") if taker is not None else fallback_fee_pct
    return fees


async def load_account_trading_fees(exchange, symbols: Iterable[str], fallback_fee_pct: Decimal) -> dict[str, Decimal]:
    fees: dict[str, Decimal] = {}
    symbol_list = list(symbols)

    if getattr(exchange, "has", {}).get("fetchTradingFee"):
        for symbol in symbol_list:
            try:
                fee = await exchange.fetch_trading_fee(symbol)
                taker = fee.get("taker") if fee else None
                if taker is not None:
                    fees[symbol] = Decimal(str(taker)) * Decimal("100")
            except Exception:
                continue

    if len(fees) < len(symbol_list) and getattr(exchange, "has", {}).get("fetchTradingFees"):
        try:
            all_fees = await exchange.fetch_trading_fees()
            for symbol in symbol_list:
                fee = all_fees.get(symbol) if all_fees else None
                taker = fee.get("taker") if fee else None
                if taker is not None:
                    fees[symbol] = Decimal(str(taker)) * Decimal("100")
        except Exception:
            pass

    if len(fees) < len(symbol_list):
        market_fees = await load_markets_and_fees(exchange, symbol_list, fallback_fee_pct)
        for symbol in symbol_list:
            fees.setdefault(symbol, market_fees.get(symbol, fallback_fee_pct))

    return fees


def load_account_trading_fees_sync(exchange_id: str, symbols: Iterable[str], fallback_fee_pct: Decimal) -> dict[str, Decimal]:
    exchange = make_sync_exchange(exchange_id, private=True)
    symbol_list = list(symbols)
    fees: dict[str, Decimal] = {}
    try:
        if exchange.has.get("fetchTradingFee"):
            for symbol in symbol_list:
                try:
                    fee = exchange.fetch_trading_fee(symbol)
                    taker = fee.get("taker") if fee else None
                    if taker is not None:
                        fees[symbol] = Decimal(str(taker)) * Decimal("100")
                except Exception:
                    continue

        if len(fees) < len(symbol_list) and exchange.has.get("fetchTradingFees"):
            try:
                all_fees = exchange.fetch_trading_fees()
                for symbol in symbol_list:
                    fee = all_fees.get(symbol) if all_fees else None
                    taker = fee.get("taker") if fee else None
                    if taker is not None:
                        fees[symbol] = Decimal(str(taker)) * Decimal("100")
            except Exception:
                pass

        if len(fees) < len(symbol_list):
            markets = exchange.load_markets()
            for symbol in symbol_list:
                market = markets.get(symbol)
                taker = market.get("taker") if market else None
                fees.setdefault(
                    symbol,
                    Decimal(str(taker)) * Decimal("100") if taker is not None else fallback_fee_pct,
                )
        return fees
    finally:
        try:
            exchange.close()
        except Exception:
            pass


async def load_market(exchange, symbol: str) -> Decimal | None:
    fees = await load_markets_and_fees(exchange, [symbol], Decimal("0"))
    return fees.get(symbol)


def weighted_price(levels: list, amount_quote: Decimal, side: str) -> tuple[Decimal, Decimal] | None:
    remaining_quote = amount_quote
    total_base = Decimal("0")
    total_quote = Decimal("0")
    for price_raw, volume_raw, *_ in levels:
        price = Decimal(str(price_raw))
        volume = Decimal(str(volume_raw))
        if price <= 0 or volume <= 0:
            continue
        level_quote = price * volume
        spend_quote = min(remaining_quote, level_quote)
        base = spend_quote / price
        total_base += base
        total_quote += spend_quote
        remaining_quote -= spend_quote
        if remaining_quote <= 0:
            break

    if total_base <= 0:
        return None
    if side == "buy" and total_quote < amount_quote * Decimal("0.95"):
        return None
    return total_quote / total_base, total_base


def compact_symbol(symbol: str) -> str:
    return symbol.replace("/", "").replace("-", "").upper()


def okx_symbol(symbol: str) -> str:
    return symbol.replace("/", "-").upper()


async def fetch_direct_order_book(exchange_id: str, symbol: str, limit: int) -> dict | None:
    compact = compact_symbol(symbol)
    headers = {"User-Agent": "arb-bot/1.0"}
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())

    if exchange_id == "binance":
        url = f"https://api.binance.com/api/v3/depth?symbol={compact}&limit={limit}"
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            async with session.get(url, timeout=10) as response:
                response.raise_for_status()
                data = await response.json()
                return {"bids": data.get("bids", []), "asks": data.get("asks", [])}

    if exchange_id == "okx":
        url = f"https://www.okx.com/api/v5/market/books?instId={okx_symbol(symbol)}&sz={limit}"
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            async with session.get(url, timeout=10) as response:
                response.raise_for_status()
                data = await response.json()
                books = (data.get("data") or [{}])[0]
                return {"bids": books.get("bids", []), "asks": books.get("asks", [])}

    if exchange_id == "bitget":
        url = f"https://api.bitget.com/api/v2/spot/market/orderbook?symbol={compact}&type=step0&limit={limit}"
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            async with session.get(url, timeout=10) as response:
                response.raise_for_status()
                data = await response.json()
                book = data.get("data") or {}
                return {"bids": book.get("bids", []), "asks": book.get("asks", [])}

    return None


async def fetch_quote(
    exchange,
    symbol: str,
    trade_size_quote: Decimal,
    fallback_fee_pct: Decimal,
    orderbook_limit: int = 10,
) -> Quote | None:
    try:
        exchange._arb_last_quote_error = None
        if exchange.id in {"binance", "okx", "bitget"}:
            orderbook = await fetch_direct_order_book(exchange.id, symbol, orderbook_limit)
        else:
            orderbook = None
        if orderbook is None:
            orderbook = await exchange.fetch_order_book(symbol, limit=orderbook_limit)

        bids = orderbook.get("bids") or []
        asks = orderbook.get("asks") or []
        if not bids or not asks:
            print(f"[skip] {exchange.id}: missing order book for {symbol}")
            return None

        ask_weighted = weighted_price(asks, trade_size_quote, "buy")
        bid_weighted = weighted_price(bids, trade_size_quote, "sell")
        if ask_weighted is None or bid_weighted is None:
            print(f"[skip] {exchange.id}: insufficient depth for {symbol}")
            return None

        ask, ask_volume = ask_weighted
        bid, bid_volume = bid_weighted
        fees = getattr(exchange, "_arb_taker_fee_pct_by_symbol", {})
        taker_fee_pct = fees.get(symbol, fallback_fee_pct)
        return Quote(
            exchange_id=exchange.id,
            symbol=symbol,
            bid=bid,
            ask=ask,
            bid_volume=bid_volume,
            ask_volume=ask_volume,
            taker_fee_pct=taker_fee_pct,
            timestamp=datetime.now(timezone.utc),
        )
    except Exception as exc:
        exchange._arb_last_quote_error = f"{type(exc).__name__}: {exc}"
        print(f"[warn] {exchange.id} {symbol}: {exchange._arb_last_quote_error}")
        return None


def find_opportunities(
    quotes: Iterable[Quote],
    trade_size_quote: Decimal,
    min_net_profit_pct: Decimal,
    slippage_pct: Decimal,
) -> list[Opportunity]:
    opportunities: list[Opportunity] = []
    quote_list = list(quotes)

    for buy in quote_list:
        for sell in quote_list:
            if buy.exchange_id == sell.exchange_id or buy.symbol != sell.symbol:
                continue
            if buy.ask <= 0 or sell.bid <= 0 or sell.bid <= buy.ask:
                continue

            gross_profit_pct = ((sell.bid - buy.ask) / buy.ask) * Decimal("100")
            cost_pct = buy.taker_fee_pct + sell.taker_fee_pct + (slippage_pct * Decimal("2"))
            net_profit_pct = gross_profit_pct - cost_pct
            if net_profit_pct < min_net_profit_pct:
                continue

            base_amount = min(buy.ask_volume, sell.bid_volume, trade_size_quote / buy.ask)
            quote_amount = base_amount * buy.ask
            estimated_profit_quote = quote_amount * (net_profit_pct / Decimal("100"))
            opportunities.append(
                Opportunity(
                    symbol=buy.symbol,
                    buy_exchange=buy.exchange_id,
                    sell_exchange=sell.exchange_id,
                    buy_price=buy.ask,
                    sell_price=sell.bid,
                    base_amount=base_amount,
                    quote_amount=quote_amount,
                    gross_profit_pct=gross_profit_pct,
                    net_profit_pct=net_profit_pct,
                    estimated_profit_quote=estimated_profit_quote,
                )
            )

    return sorted(opportunities, key=lambda item: item.net_profit_pct, reverse=True)


def fmt_pct(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.0001'))}%"


def print_opportunities(opportunities: list[Opportunity]) -> None:
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    if not opportunities:
        print(f"[{now}] no net opportunity")
        return

    for item in opportunities[:5]:
        print(
            f"[{now}] {item.symbol}: buy {item.buy_exchange} @ {item.buy_price} -> "
            f"sell {item.sell_exchange} @ {item.sell_price} | "
            f"net {fmt_pct(item.net_profit_pct)} | paper profit ~{item.estimated_profit_quote.quantize(Decimal('0.0001'))}"
        )


async def prepare_exchanges(config: Config):
    private = config.mode == "live"
    exchanges = [make_exchange(exchange_id, private=private) for exchange_id in config.exchanges]
    ready = []
    try:
        for exchange in exchanges:
            try:
                fees = await load_markets_and_fees(exchange, config.symbols, config.default_taker_fee_pct)
                if not fees:
                    fees = {symbol: config.default_taker_fee_pct for symbol in config.symbols}
                exchange._arb_taker_fee_pct_by_symbol = fees
                ready.append(exchange)
                print(f"[ready] {exchange.id}: {len(fees)} symbols")
            except Exception as exc:
                exchange._arb_taker_fee_pct_by_symbol = {
                    symbol: config.default_taker_fee_pct for symbol in config.symbols
                }
                ready.append(exchange)
                print(
                    f"[warn] {exchange.id}: market metadata failed, using direct public order book fallback: "
                    f"{type(exc).__name__}: {exc}"
                )

        if len(ready) < 2:
            raise RuntimeError("Need at least two exchanges with configured symbols")
        return ready
    except Exception:
        await asyncio.gather(*(exchange.close() for exchange in ready), return_exceptions=True)
        raise


async def run() -> None:
    config = load_config()
    if config.mode == "live" and (not config.live_trading or config.live_confirm != "I_UNDERSTAND_REAL_ORDERS"):
        raise RuntimeError("Live trading is locked. Set LIVE_TRADING=true and LIVE_CONFIRM=I_UNDERSTAND_REAL_ORDERS.")

    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    exchanges = await prepare_exchanges(config)
    print(f"[start] scanning {', '.join(config.symbols)} on {', '.join(exchange.id for exchange in exchanges)}")

    try:
        while not stop_event.is_set():
            quotes = await asyncio.gather(
                *[
                    fetch_quote(exchange, symbol, config.trade_size_quote, config.default_taker_fee_pct, config.orderbook_limit)
                    for exchange in exchanges
                    for symbol in config.symbols
                ]
            )
            opportunities = find_opportunities(
                [quote for quote in quotes if quote is not None],
                config.trade_size_quote,
                config.min_net_profit_pct,
                config.slippage_pct,
            )
            print_opportunities(opportunities)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=config.poll_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        await asyncio.gather(*(exchange.close() for exchange in exchanges), return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run())

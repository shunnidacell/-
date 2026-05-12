from decimal import Decimal

from arb_bot import Quote, find_opportunities


def quote(exchange_id: str, bid: str, ask: str, fee: str = "0.10") -> Quote:
    return Quote(
        exchange_id=exchange_id,
        symbol="BTC/USDT",
        bid=Decimal(bid),
        ask=Decimal(ask),
        bid_volume=Decimal("10"),
        ask_volume=Decimal("10"),
        taker_fee_pct=Decimal(fee),
        timestamp=None,
    )


def test_find_opportunities_after_costs():
    opportunities = find_opportunities(
        [
            quote("cheap", "100", "100"),
            quote("rich", "101", "101"),
        ],
        trade_size_quote=Decimal("1000"),
        min_net_profit_pct=Decimal("0.50"),
        slippage_pct=Decimal("0.03"),
    )

    assert len(opportunities) == 1
    assert opportunities[0].buy_exchange == "cheap"
    assert opportunities[0].sell_exchange == "rich"
    assert opportunities[0].symbol == "BTC/USDT"
    assert opportunities[0].gross_profit_pct == Decimal("1.00")
    assert opportunities[0].net_profit_pct == Decimal("0.74")
    assert opportunities[0].estimated_profit_quote == Decimal("7.4000")


def test_ignores_spread_that_does_not_clear_costs():
    opportunities = find_opportunities(
        [
            quote("cheap", "100", "100"),
            quote("rich", "100.2", "100.2"),
        ],
        trade_size_quote=Decimal("1000"),
        min_net_profit_pct=Decimal("0.10"),
        slippage_pct=Decimal("0.03"),
    )

    assert opportunities == []

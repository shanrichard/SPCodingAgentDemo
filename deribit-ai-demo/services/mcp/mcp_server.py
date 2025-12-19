import os
import httpx
from fastmcp import FastMCP

DERIBIT_API = "https://www.deribit.com/api/v2"

mcp = FastMCP("deribit-tools")


def _get(endpoint: str, params: dict = None):
    """调用 Deribit 公开 API"""
    url = f"{DERIBIT_API}{endpoint}"
    resp = httpx.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json().get("result", [])


@mcp.tool()
def list_currencies():
    """
    列出 Deribit 支持的所有币种。
    返回: [{"currency": "BTC", "currency_long": "Bitcoin", ...}, ...]
    """
    return _get("/public/get_currencies")


@mcp.tool()
def list_instruments(currency: str = "BTC", kind: str = "all", expired: bool = False):
    """
    列出指定币种的合约。

    Args:
        currency: 币种 (BTC, ETH, SOL, USDC, USDT, EURR, any_combo 等)
        kind: 合约类型 (future, option, spot, future_combo, option_combo, combo, all)
        expired: 是否包含已过期合约

    返回合约列表，每个包含 instrument_name, kind, base_currency, quote_currency 等字段
    """
    params = {"currency": currency, "expired": str(expired).lower()}
    if kind != "all":
        params["kind"] = kind
    return _get("/public/get_instruments", params)


@mcp.tool()
def get_instrument(instrument_name: str):
    """
    获取单个合约的详细信息。

    Args:
        instrument_name: 合约名称 (如 BTC-PERPETUAL, BTC-27DEC24-100000-C)

    返回合约详情，包括 tick_size, min_trade_amount, contract_size 等
    """
    return _get("/public/get_instrument", {"instrument_name": instrument_name})


@mcp.tool()
def get_index_price_names():
    """
    列出所有可用的指数价格名称。
    用于订阅 deribit_price_index.{index_name} 频道。
    """
    return _get("/public/get_index_price_names")


@mcp.tool()
def get_book_summary(currency: str = "BTC", kind: str = "future"):
    """
    获取指定币种所有合约的订单簿摘要（当前最优买卖价、24h成交量等）。

    Args:
        currency: 币种
        kind: 合约类型 (future, option, spot, all)
    """
    return _get("/public/get_book_summary_by_currency", {"currency": currency, "kind": kind})


@mcp.tool()
def get_ticker(instrument_name: str):
    """
    获取单个合约的实时行情快照。

    Args:
        instrument_name: 合约名称

    返回包含 last_price, mark_price, best_bid/ask, funding_rate 等字段
    """
    return _get("/public/ticker", {"instrument_name": instrument_name})


@mcp.tool()
def get_order_book(instrument_name: str, depth: int = 10):
    """
    获取合约的订单簿深度数据。

    Args:
        instrument_name: 合约名称
        depth: 深度档位 (1-5000)
    """
    return _get("/public/get_order_book", {"instrument_name": instrument_name, "depth": depth})


@mcp.tool()
def get_tradingview_chart_data(instrument_name: str, resolution: str = "60", start_timestamp: int = None, end_timestamp: int = None):
    """
    获取 K 线数据（TradingView 格式）。

    Args:
        instrument_name: 合约名称
        resolution: K线周期 (1, 3, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1D)
        start_timestamp: 开始时间戳(毫秒)
        end_timestamp: 结束时间戳(毫秒)
    """
    import time
    params = {"instrument_name": instrument_name, "resolution": resolution}
    if end_timestamp is None:
        end_timestamp = int(time.time() * 1000)
    if start_timestamp is None:
        # 默认取最近 100 根 K 线
        mins = {"1": 1, "3": 3, "5": 5, "10": 10, "15": 15, "30": 30, "60": 60, "120": 120, "180": 180, "360": 360, "720": 720, "1D": 1440}
        period_mins = mins.get(resolution, 60)
        start_timestamp = end_timestamp - (period_mins * 60 * 1000 * 100)
    params["start_timestamp"] = start_timestamp
    params["end_timestamp"] = end_timestamp
    return _get("/public/get_tradingview_chart_data", params)


@mcp.tool()
def get_funding_rate_history(instrument_name: str, start_timestamp: int = None, end_timestamp: int = None):
    """
    获取永续合约的历史资金费率。

    Args:
        instrument_name: 永续合约名称 (如 BTC-PERPETUAL)
        start_timestamp: 开始时间戳(毫秒)
        end_timestamp: 结束时间戳(毫秒)
    """
    import time
    params = {"instrument_name": instrument_name}
    if end_timestamp is None:
        end_timestamp = int(time.time() * 1000)
    if start_timestamp is None:
        start_timestamp = end_timestamp - (24 * 60 * 60 * 1000 * 7)  # 最近7天
    params["start_timestamp"] = start_timestamp
    params["end_timestamp"] = end_timestamp
    return _get("/public/get_funding_rate_history", params)


@mcp.tool()
def channel_cheatsheet():
    """
    WebSocket 公开订阅频道速查表。
    """
    return {
        "行情数据": {
            "ticker.{instrument}.{interval}": "实时行情 (interval: 100ms, agg2, raw)",
            "incremental_ticker.{instrument}": "增量行情更新",
            "book.{instrument}.{interval}": "订单簿快照 (interval: 100ms, agg2, raw)",
            "book.{instrument}.{group}.{depth}.{interval}": "分组订单簿 (group: none/1/2/5/10/...)",
            "trades.{instrument}.{interval}": "成交记录",
            "trades.{kind}.{currency}.{interval}": "按类型聚合成交 (kind: future/option/spot)",
            "chart.trades.{instrument}.{resolution}": "K线 (resolution: 1/3/5/.../1D)",
            "quote.{instrument}": "报价",
        },
        "指数与波动率": {
            "deribit_price_index.{index}": "价格指数 (btc_usd, eth_usd, sol_usd 等)",
            "deribit_price_ranking.{index}": "价格排名",
            "deribit_price_statistics.{index}": "价格统计",
            "deribit_volatility_index.{index}": "波动率指数 (btc_usd, eth_usd)",
            "estimated_expiration_price.{index}": "预估到期价格",
        },
        "期权专用": {
            "markprice.options.{index}": "期权标记价格",
        },
        "合约状态": {
            "instrument.state.{kind}.{currency}": "合约状态变化 (kind: future/option/spot/combo)",
        },
        "平台状态": {
            "platform_state": "平台状态",
            "platform_state.public_methods_state": "API 方法可用状态",
        },
        "大宗交易": {
            "block_rfq.trades.{currency}": "RFQ 成交",
        },
        "示例": [
            "ticker.BTC-PERPETUAL.100ms",
            "ticker.BTC-27DEC24-100000-C.100ms",
            "book.ETH-PERPETUAL.none.10.100ms",
            "trades.option.BTC.100ms",
            "deribit_volatility_index.btc_usd",
        ]
    }


@mcp.tool()
def get_ticker_fields():
    """
    Ticker 订阅返回的字段说明。
    """
    return {
        "通用字段": {
            "instrument_name": "合约名称",
            "last_price": "最新成交价",
            "mark_price": "标记价格",
            "index_price": "指数价格",
            "best_bid_price": "买一价",
            "best_ask_price": "卖一价",
            "best_bid_amount": "买一量",
            "best_ask_amount": "卖一量",
            "timestamp": "时间戳(毫秒)",
        },
        "成交量": {
            "volume_usd": "24h USD成交量",
            "volume_notional": "24h名义成交量",
            "price_change": "24h涨跌幅(%)",
            "high": "24h最高价",
            "low": "24h最低价",
        },
        "永续合约专用": {
            "funding_8h": "8小时资金费率",
            "current_funding": "当前资金费率",
            "interest_rate": "利率",
        },
        "期货/期权": {
            "settlement_price": "结算价",
            "open_interest": "持仓量",
            "estimated_delivery_price": "预估交割价",
        },
        "期权专用": {
            "underlying_price": "标的价格",
            "underlying_index": "标的指数",
            "mark_iv": "标记隐含波动率",
            "bid_iv": "买方隐含波动率",
            "ask_iv": "卖方隐含波动率",
            "greeks": "希腊字母 (delta, gamma, vega, theta, rho)",
        }
    }


if __name__ == "__main__":
    mcp.run(transport="sse")

import asyncio
import json
import os
import re
from typing import Dict, Set
import websockets
from fastapi import WebSocket, WebSocketDisconnect

DERIBIT_WS_URL = os.getenv("DERIBIT_WS_URL", "wss://streams.deribit.com/ws/api/v2")

# 允许所有 Deribit 公共订阅频道
ALLOWED_PREFIXES = (
    # 行情数据
    "ticker.",                      # ticker.{instrument}.{interval}
    "incremental_ticker.",          # incremental_ticker.{instrument}
    "book.",                        # book.{instrument}.{interval} 或分组深度
    "trades.",                      # trades.{instrument}.{interval}
    "chart.trades.",                # chart.trades.{instrument}.{resolution}
    "quote.",                       # quote.{instrument}

    # 指数与波动率
    "deribit_price_index.",         # deribit_price_index.{index_name}
    "deribit_price_ranking.",       # deribit_price_ranking.{index_name}
    "deribit_price_statistics.",    # deribit_price_statistics.{index_name}
    "deribit_volatility_index.",    # deribit_volatility_index.{index_name}
    "estimated_expiration_price.",  # estimated_expiration_price.{index_name}

    # 合约状态
    "instrument.state.",            # instrument.state.{kind}.{currency}

    # 平台状态
    "platform_state",               # platform_state 或 platform_state.public_methods_state

    # 大宗交易
    "block_rfq.",                   # block_rfq.trades.{currency}

    # 期权相关
    "markprice.options.",           # markprice.options.{index_name}
)

CHANNEL_RE = re.compile(r"^[a-zA-Z0-9_.\-]+$")


class MarketHub:
    def __init__(self):
        self._ws = None
        self._recv_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

        # channel -> set(WebSocket clients)
        self._subs: Dict[str, Set[WebSocket]] = {}
        self._all_channels: Set[str] = set()

        self._req_id = 1
        self._running = False

    async def start(self):
        self._running = True
        await self._connect()
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def stop(self):
        self._running = False
        if self._recv_task:
            self._recv_task.cancel()
        if self._ws:
            await self._ws.close()

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _connect(self):
        self._ws = await websockets.connect(DERIBIT_WS_URL, ping_interval=None)
        # set heartbeat interval 30s
        await self._ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "public/set_heartbeat",
            "params": {"interval": 30}
        }))

        # resubscribe all channels after reconnect
        if self._all_channels:
            await self._ws.send(json.dumps({
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "public/subscribe",
                "params": {"channels": sorted(self._all_channels)}
            }))

    async def _recv_loop(self):
        while self._running:
            try:
                raw = await self._ws.recv()
                msg = json.loads(raw)

                # heartbeat handling
                if msg.get("method") == "heartbeat":
                    params = msg.get("params") or {}
                    if params.get("type") == "test_request":
                        # respond with public/test
                        await self._ws.send(json.dumps({
                            "jsonrpc": "2.0",
                            "id": self._next_id(),
                            "method": "public/test",
                            "params": {}
                        }))
                    continue

                # subscription data
                if msg.get("method") == "subscription":
                    params = msg.get("params") or {}
                    channel = params.get("channel")
                    if not channel:
                        continue
                    # fan-out to clients subscribed to that channel
                    clients = self._subs.get(channel, set()).copy()
                    for client in clients:
                        try:
                            await client.send_text(raw)
                        except Exception:
                            pass
            except Exception:
                # reconnect loop
                await asyncio.sleep(1.0)
                try:
                    await self._connect()
                except Exception:
                    await asyncio.sleep(2.0)

    def _validate_channel(self, channel: str) -> bool:
        if not channel or not CHANNEL_RE.match(channel):
            return False
        if not channel.startswith(ALLOWED_PREFIXES):
            return False
        return True

    async def _subscribe_deribit(self, channels: Set[str]):
        if not channels:
            return
        await self._ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "public/subscribe",
            "params": {"channels": sorted(channels)}
        }))

    async def _unsubscribe_deribit(self, channels: Set[str]):
        if not channels:
            return
        await self._ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "public/unsubscribe",
            "params": {"channels": sorted(channels)}
        }))

    async def ws_handler(self, ws: WebSocket):
        await ws.accept()
        try:
            while True:
                data = await ws.receive_text()
                req = json.loads(data)

                op = req.get("op")
                channels = req.get("channels") or []
                channels = [c for c in channels if isinstance(c, str)]

                valid = [c for c in channels if self._validate_channel(c)]
                if not valid:
                    await ws.send_text(json.dumps({"type": "error", "error": "no valid channels"}))
                    continue

                async with self._lock:
                    if op == "subscribe":
                        newly_added = set()
                        for c in valid:
                            self._subs.setdefault(c, set()).add(ws)
                            if c not in self._all_channels:
                                self._all_channels.add(c)
                                newly_added.add(c)
                        await self._subscribe_deribit(newly_added)
                        await ws.send_text(json.dumps({"type": "ok", "op": "subscribe", "channels": valid}))

                    elif op == "unsubscribe":
                        newly_removed = set()
                        for c in valid:
                            if c in self._subs:
                                self._subs[c].discard(ws)
                                if not self._subs[c]:
                                    self._subs.pop(c, None)
                                    if c in self._all_channels:
                                        self._all_channels.remove(c)
                                        newly_removed.add(c)
                        await self._unsubscribe_deribit(newly_removed)
                        await ws.send_text(json.dumps({"type": "ok", "op": "unsubscribe", "channels": valid}))

                    else:
                        await ws.send_text(json.dumps({"type": "error", "error": "unknown op"}))

        except WebSocketDisconnect:
            pass
        finally:
            # cleanup: remove ws from all channels
            async with self._lock:
                removed = set()
                for c, clients in list(self._subs.items()):
                    if ws in clients:
                        clients.discard(ws)
                        if not clients:
                            self._subs.pop(c, None)
                            if c in self._all_channels:
                                self._all_channels.remove(c)
                                removed.add(c)
                await self._unsubscribe_deribit(removed)

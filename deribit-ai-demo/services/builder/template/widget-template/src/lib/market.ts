type Handler = (msg: any) => void;

// ============ REST API Types ============

export interface Instrument {
  instrument_name: string;
  kind: string;
  option_type?: "call" | "put";
  strike?: number;
  expiration_timestamp?: number;
  is_active: boolean;
}

export interface InstrumentSummary {
  instrument_name: string;
  mark_price: number | null;
  mark_iv: number | null;
  underlying_price: number | null;
  underlying_index: string | null;
  bid_price: number | null;
  ask_price: number | null;
  mid_price: number | null;
  open_interest: number | null;
  volume_usd: number | null;
}

export interface Expiration {
  timestamp: number;
  date: string;
  label: string;
}

// ============ REST API Functions ============

function getBasePath(): string {
  // Detect base path from current URL (e.g., /spcoding/widgets/xxx/... -> /spcoding)
  const pathMatch = location.pathname.match(/^(\/[^/]+)\/widgets\//);
  return pathMatch ? pathMatch[1] : "";
}

/**
 * 获取合约列表
 */
export async function getInstruments(
  currency: string = "BTC",
  kind: string = "option"
): Promise<Instrument[]> {
  const basePath = getBasePath();
  const resp = await fetch(`${basePath}/api/instruments?currency=${currency}&kind=${kind}`);
  const data = await resp.json();
  return data.instruments || [];
}

/**
 * 获取合约摘要（含 IV，不含 Greeks）
 */
export async function getInstrumentsSummary(
  currency: string = "BTC",
  kind: string = "option"
): Promise<InstrumentSummary[]> {
  const basePath = getBasePath();
  const resp = await fetch(`${basePath}/api/instruments/summary?currency=${currency}&kind=${kind}`);
  const data = await resp.json();
  return data.instruments || [];
}

/**
 * 获取所有期权到期日
 */
export async function getExpirations(currency: string = "BTC"): Promise<Expiration[]> {
  const basePath = getBasePath();
  const resp = await fetch(`${basePath}/api/instruments/expirations?currency=${currency}`);
  const data = await resp.json();
  return data.expirations || [];
}

// ============ Utility Functions ============

/**
 * 从期权名称解析到期日时间戳
 * @example parseExpiry("BTC-26DEC25-100000-C") => 1766390400000
 */
export function parseExpiry(instrumentName: string): number {
  const match = instrumentName.match(/-(\d{2})([A-Z]{3})(\d{2})-/);
  if (!match) return 0;
  const [, day, mon, year] = match;
  const months: Record<string, number> = {
    JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
    JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11
  };
  return new Date(2000 + parseInt(year), months[mon], parseInt(day), 8, 0, 0).getTime();
}

/**
 * 从期权名称解析行权价
 * @example parseStrike("BTC-26DEC25-100000-C") => 100000
 */
export function parseStrike(instrumentName: string): number {
  const match = instrumentName.match(/-(\d+)-[CP]$/);
  return match ? parseInt(match[1]) : 0;
}

/**
 * 从期权名称解析类型
 * @example parseOptionType("BTC-26DEC25-100000-C") => "call"
 */
export function parseOptionType(instrumentName: string): "call" | "put" | null {
  if (instrumentName.endsWith("-C")) return "call";
  if (instrumentName.endsWith("-P")) return "put";
  return null;
}

/**
 * 按到期日分组期权
 */
export function groupByExpiry<T extends { instrument_name: string }>(
  instruments: T[]
): Map<number, T[]> {
  const groups = new Map<number, T[]>();
  for (const inst of instruments) {
    const expiry = parseExpiry(inst.instrument_name);
    if (!groups.has(expiry)) groups.set(expiry, []);
    groups.get(expiry)!.push(inst);
  }
  return groups;
}

/**
 * 按行权价分组期权
 */
export function groupByStrike<T extends { instrument_name: string }>(
  instruments: T[]
): Map<number, T[]> {
  const groups = new Map<number, T[]>();
  for (const inst of instruments) {
    const strike = parseStrike(inst.instrument_name);
    if (!groups.has(strike)) groups.set(strike, []);
    groups.get(strike)!.push(inst);
  }
  return groups;
}

// ============ WebSocket Client ============

export class MarketClient {
  private ws: WebSocket | null = null;
  private handlers: Map<string, Set<Handler>> = new Map();
  private pendingSubscriptions: string[] = [];

  connect() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const proto = location.protocol === "https:" ? "wss" : "ws";
    // Detect base path from current URL (e.g., /spcoding/widgets/xxx/... -> /spcoding)
    const pathMatch = location.pathname.match(/^(\/[^/]+)\/widgets\//);
    const basePath = pathMatch ? pathMatch[1] : "";
    const url = `${proto}://${location.host}${basePath}/ws/market`;
    this.ws = new WebSocket(url);

    this.ws.onopen = () => {
      // Send any pending subscriptions
      if (this.pendingSubscriptions.length > 0) {
        this.ws?.send(JSON.stringify({ op: "subscribe", channels: this.pendingSubscriptions }));
        this.pendingSubscriptions = [];
      }
    };

    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        // Deribit subscription payload shape passthrough:
        // { jsonrpc:"2.0", method:"subscription", params:{ channel, data } }
        const ch = msg?.params?.channel;
        if (!ch) return;
        const hs = this.handlers.get(ch);
        if (hs) hs.forEach((h) => h(msg));
      } catch {
        // ignore parse errors
      }
    };

    this.ws.onclose = () => {
      // Attempt reconnect after a delay
      setTimeout(() => this.connect(), 2000);
    };

    this.ws.onerror = () => {
      // Will trigger onclose
    };
  }

  subscribe(channels: string[], handler: Handler) {
    this.connect();

    channels.forEach((c) => {
      if (!this.handlers.has(c)) this.handlers.set(c, new Set());
      this.handlers.get(c)!.add(handler);
    });

    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ op: "subscribe", channels }));
    } else {
      // Queue for when connection opens
      this.pendingSubscriptions.push(...channels);
    }
  }

  unsubscribe(channels: string[], handler: Handler) {
    channels.forEach((c) => {
      this.handlers.get(c)?.delete(handler);
    });

    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ op: "unsubscribe", channels }));
    }
  }
}

export const market = new MarketClient();

type Handler = (msg: any) => void;

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

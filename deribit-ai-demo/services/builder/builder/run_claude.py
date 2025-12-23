import os
import shutil
import signal
import subprocess
import json
import uuid
from pathlib import Path

TEMPLATE_DIR = Path("/app/template/widget-template")
MCP_URL = os.getenv("MCP_URL", "http://mcp:7001/mcp")


def _terminate_process(proc, log_func, timeout=10):
    """Gracefully terminate a subprocess and its children, escalating to SIGKILL if needed.

    Uses process group (PGID) to kill all child processes spawned by the subprocess
    (e.g., esbuild, chrome-headless, git processes spawned by Claude Code).
    """
    if proc.poll() is not None:
        return  # Already terminated

    try:
        # Get the process group ID (same as proc.pid since we used setsid)
        pgid = os.getpgid(proc.pid) if hasattr(os, 'getpgid') else None

        # Try graceful termination first - send SIGTERM to entire process group
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGTERM)
                log_func(f"    Sent SIGTERM to process group {pgid}")
            except ProcessLookupError:
                pass  # Process already gone
            except PermissionError:
                # Fallback to just terminating the main process
                proc.terminate()
        else:
            proc.terminate()

        try:
            proc.wait(timeout=timeout)
            log_func(f"    Process terminated gracefully")
        except subprocess.TimeoutExpired:
            # Force kill the entire process group if graceful termination fails
            log_func(f"    Process didn't terminate, sending SIGKILL to process group")
            if pgid is not None:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
            else:
                proc.kill()
            proc.wait(timeout=5)
            log_func(f"    Process group killed")
    except Exception as e:
        log_func(f"    Error terminating process: {e}")
        # Last resort: try to kill anyway
        try:
            proc.kill()
            proc.wait()
        except Exception:
            pass


def _run_claude(prompt: str, ws_dir: Path, log_func, session_id: str = None, resume: bool = False):
    """Run Claude Code with streaming output. Returns session_id."""
    env = os.environ.copy()

    # 构建命令
    cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose"]

    if resume and session_id:
        cmd.extend(["--resume", session_id])
    elif session_id:
        cmd.extend(["--session-id", session_id])

    proc = subprocess.Popen(
        cmd,
        cwd=str(ws_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=0,
        # Start new process group to allow killing child processes
        preexec_fn=os.setsid if hasattr(os, 'setsid') else None
    )

    # 实时解析 JSON 事件流
    result_session_id = session_id
    try:
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                event_type = event.get("type", "")
                log_func(f"    [DEBUG] event_type={event_type}")

                if event_type == "system":
                    subtype = event.get("subtype", "")
                    if subtype == "init":
                        log_func("    [System] Claude Code initialized")
                        # 提取 session_id
                        if "session_id" in event:
                            result_session_id = event.get("session_id")

                elif event_type == "assistant":
                    msg = event.get("message", {})
                    content = msg.get("content", [])
                    for block in content:
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            for text_line in text.split("\n")[:10]:
                                if text_line.strip():
                                    log_func(f"    [Claude] {text_line[:150]}")
                        elif block.get("type") == "tool_use":
                            tool_name = block.get("name", "unknown")
                            tool_input = block.get("input", {})
                            if tool_name == "Read":
                                log_func(f"    [Tool] Read: {tool_input.get('file_path', '')}")
                            elif tool_name == "Write":
                                log_func(f"    [Tool] Write: {tool_input.get('file_path', '')}")
                            elif tool_name == "Edit":
                                log_func(f"    [Tool] Edit: {tool_input.get('file_path', '')}")
                            elif tool_name == "Bash":
                                cmd_str = tool_input.get('command', '')[:80]
                                log_func(f"    [Tool] Bash: {cmd_str}")
                            else:
                                log_func(f"    [Tool] {tool_name}")

                elif event_type == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        log_func(f"    [Tool] Starting: {block.get('name', '')}")

                elif event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text.strip() and len(text) > 5:
                            log_func(f"    {text[:100]}")

                elif event_type == "result":
                    subtype = event.get("subtype", "")
                    if subtype == "success":
                        log_func("    [Done] Claude finished successfully")
                        if "session_id" in event:
                            result_session_id = event.get("session_id")
                    elif subtype == "error":
                        error = event.get("error", "unknown")
                        log_func(f"    [Error] {error}")

            except json.JSONDecodeError:
                if line:
                    log_func(f"    {line[:200]}")

        # Normal completion - wait for process with timeout
        try:
            proc.wait(timeout=300)
            log_func(f"    Claude exit code: {proc.returncode}")
        except subprocess.TimeoutExpired:
            log_func("    Claude process timed out after 300s, terminating...")
            _terminate_process(proc, log_func)

    except Exception as e:
        # On any exception, ensure we clean up the process
        log_func(f"    Exception during Claude execution: {e}")
        _terminate_process(proc, log_func)
        raise

    finally:
        # Ensure process is always reaped to prevent zombies
        if proc.poll() is None:
            log_func("    Cleaning up Claude process in finally block...")
            _terminate_process(proc, log_func)
        # Close stdout to release file descriptor
        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass

    return result_session_id


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None):
    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=True,
        capture_output=True,
        text=True
    )
    return p.stdout


def build_widget(widget_id: str, user_prompt: str, widgets_dir: str):
    out_root = Path(widgets_dir) / widget_id
    ws_dir = out_root / "workspace"
    log_path = out_root / "build.log"

    logs = []

    def log(msg: str):
        logs.append(msg)
        log_path.write_text("\n".join(logs), encoding="utf-8")

    try:
        log(f"[1/6] Preparing workspace for widget {widget_id}...")

        if ws_dir.exists():
            shutil.rmtree(ws_dir)
        shutil.copytree(TEMPLATE_DIR, ws_dir)

        # 给 Claude 的项目说明
        claude_md = ws_dir / "CLAUDE.md"
        claude_md.write_text(f"""# Deribit Widget 开发指南

你正在开发一个专业的 Deribit 期权/期货数据可视化 Widget（React + Vite + TypeScript）。
Deribit 是全球领先的加密货币期权交易所，用户主要关注期权相关数据分析。

---

## 第一部分：需求分析（必读）

在写任何代码之前，你必须先分析用户的真实需求，识别业务场景。

### 常见业务场景识别

| 用户可能说的话 | 真实需求 | 业务场景 |
|--------------|---------|---------|
| "做个期权链" / "看所有期权" | 按到期日和行权价组织的完整期权视图 | Options Chain |
| "Term Structure" / "期限结构" | 各到期日的 IV 曲线，支持按 Delta 筛选 | Term Structure |
| "波动率微笑" / "Volatility Smile" | 同一到期日不同行权价的 IV 分布 | Volatility Smile |
| "Greeks" / "希腊字母" | Delta/Gamma/Vega/Theta 的可视化仪表盘 | Greeks Dashboard |
| "大单" / "期权流" / "Options Flow" | 监控大额成交、异常交易 | Options Flow |
| "看永续" / "BTC-PERPETUAL" | 永续合约的实时行情 | Perpetual Ticker |
| "资金费率" / "Funding Rate" | 永续合约资金费率历史和当前值 | Funding Rate |
| "订单簿" / "深度" | 买卖盘深度可视化 | Order Book |

### 需求分析步骤

1. **识别核心场景**：用户想要什么类型的分析？
2. **确定数据需求**：需要哪些数据字段？需要实时还是快照？
3. **设计交互方式**：需要什么筛选/切换功能？
4. **规划 UI 结构**：专业金融终端是怎么展示这类数据的？

---

## 第二部分：Deribit 数据获取指南（关键！）

### ⚠️ 重要警告
- **禁止 Mock 数据**：必须连接真实的 WebSocket 获取实时数据
- **禁止直接调用 Deribit API**：必须通过 src/lib/market.ts 连接 /ws/market
- **必须验证数据**：截图时必须确认显示的是真实的、变化的市场数据

### 数据获取方式对比

| 方式 | 用途 | 是否有 Greeks | 适用场景 |
|-----|------|-------------|---------|
| WebSocket ticker | 单合约实时行情 | ✅ 有完整 Greeks | 单个期权详情、Greeks 展示 |
| WebSocket book | 订单簿深度 | ❌ | 订单簿可视化 |
| WebSocket trades | 成交记录 | ❌ | 期权流监控 |
| MCP get_book_summary | 批量期权摘要 | ❌ 只有 IV | 期权链概览、快速筛选 |
| MCP get_ticker | 单合约详情 | ✅ 有完整 Greeks | 需要 Greeks 时的补充查询 |
| MCP list_instruments | 合约列表 | ❌ | 获取所有到期日、行权价 |

### 期权命名规则

```
BTC-26DEC25-100000-C
 │     │       │    └── 类型: C=Call, P=Put
 │     │       └─────── 行权价: 100000 USD
 │     └─────────────── 到期日: 2025年12月26日
 └───────────────────── 标的: BTC
```

### WebSocket 数据结构详解

#### 1. 期权 Ticker（最重要！）

订阅频道：`ticker.BTC-26DEC25-100000-C.100ms`

```json
{{
  "params": {{
    "channel": "ticker.BTC-26DEC25-100000-C.100ms",
    "data": {{
      "instrument_name": "BTC-26DEC25-100000-C",
      "last_price": 0.0015,
      "mark_price": 0.0015,
      "best_bid_price": 0.0015,
      "best_ask_price": 0.0017,
      "best_bid_amount": 2.9,
      "best_ask_amount": 22.3,

      // ⭐ IV 数据
      "mark_iv": 47.31,        // 标记 IV (%)
      "bid_iv": 47.04,         // 买方 IV
      "ask_iv": 48.4,          // 卖方 IV

      // ⭐ Greeks 数据（只有期权有）
      "greeks": {{
        "delta": 0.07041,      // Delta: 期权价格对标的价格的敏感度
        "gamma": 0.000030,     // Gamma: Delta 的变化率
        "vega": 12.77811,      // Vega: 对 IV 的敏感度
        "theta": -73.13171,    // Theta: 时间衰减
        "rho": 0.69437         // Rho: 对利率的敏感度
      }},

      // 标的信息
      "underlying_price": 89026.51,
      "underlying_index": "BTC-26DEC25",
      "index_price": 88961.17,

      // 其他
      "open_interest": 1315.3,
      "timestamp": 1766378910886
    }}
  }}
}}
```

#### 2. 永续合约 Ticker

订阅频道：`ticker.BTC-PERPETUAL.100ms`

```json
{{
  "params": {{
    "data": {{
      "instrument_name": "BTC-PERPETUAL",
      "last_price": 97234.5,
      "mark_price": 97230.12,
      "index_price": 97228.45,
      "best_bid_price": 97230.0,
      "best_ask_price": 97231.0,

      // ⭐ 资金费率（只有永续有）
      "funding_8h": 0.0001,      // 8小时资金费率
      "current_funding": 0.00008, // 当前资金费率

      "open_interest": 45678.9,
      "volume_usd": 1234567890,
      "price_change": 2.45        // 24h 涨跌幅 (%)
    }}
  }}
}}
```

#### 3. 期权成交流（Options Flow）

订阅频道：`trades.option.BTC.100ms`（所有 BTC 期权的成交）

```json
{{
  "params": {{
    "channel": "trades.option.BTC.100ms",
    "data": [
      {{
        "instrument_name": "BTC-26DEC25-100000-C",
        "price": 0.0015,
        "amount": 10.5,           // 成交数量
        "direction": "buy",       // buy 或 sell
        "timestamp": 1766378910886,
        "trade_id": "123456",
        "iv": 47.5,               // 成交时的 IV
        "index_price": 88961.17
      }},
      // ... 可能有多笔成交
    ]
  }}
}}
```

#### 4. 订单簿

订阅频道：`book.BTC-PERPETUAL.none.10.100ms`

```json
{{
  "params": {{
    "data": {{
      "bids": [[97230.0, 5.5], [97229.0, 3.2], ...],  // [价格, 数量]
      "asks": [[97231.0, 4.1], [97232.0, 2.8], ...],
      "timestamp": 1766378910886
    }}
  }}
}}
```

### 获取所有期权的方法（REST API）

Widget 运行时可以通过 REST API 获取合约列表，**这些函数已经在 market.ts 中提供**。

#### API 1：获取合约列表

```typescript
import {{ getInstruments, getExpirations }} from "./lib/market";

// 获取所有 BTC 期权
const options = await getInstruments("BTC", "option");
// 返回: Instrument[]

// 获取所有到期日
const expirations = await getExpirations("BTC");
// 返回: Expiration[] = [{{ timestamp, date, label }}, ...]
```

**Instrument 结构**：
```typescript
interface Instrument {{
  instrument_name: string;    // "BTC-26DEC25-100000-C"
  kind: string;               // "option"
  option_type?: "call" | "put";
  strike?: number;            // 100000
  expiration_timestamp?: number;
  is_active: boolean;
}}
```

#### API 2：获取合约摘要（含 IV，无 Greeks）

```typescript
import {{ getInstrumentsSummary }} from "./lib/market";

const summaries = await getInstrumentsSummary("BTC", "option");
```

**InstrumentSummary 结构**：
```typescript
interface InstrumentSummary {{
  instrument_name: string;
  mark_price: number | null;
  mark_iv: number | null;      // ⭐ 隐含波动率 (%)
  underlying_price: number | null;
  bid_price: number | null;
  ask_price: number | null;
  open_interest: number | null;
  volume_usd: number | null;
  // ❌ 没有 Greeks！
}}
```

#### 工具函数（已提供）

```typescript
import {{
  parseExpiry,      // 从名称解析到期日时间戳
  parseStrike,      // 从名称解析行权价
  parseOptionType,  // 从名称解析 call/put
  groupByExpiry,    // 按到期日分组
  groupByStrike,    // 按行权价分组
}} from "./lib/market";

// 示例
const expiry = parseExpiry("BTC-26DEC25-100000-C");  // => timestamp
const strike = parseStrike("BTC-26DEC25-100000-C");  // => 100000
const type = parseOptionType("BTC-26DEC25-100000-C"); // => "call"

// 按到期日分组
const byExpiry = groupByExpiry(options);  // Map<timestamp, Instrument[]>
```

### ⭐ Term Structure 实现指南

Term Structure（期限结构）显示各到期日的 IV 曲线，支持按 Delta 切换。

**核心挑战**：需要按 Delta 筛选期权，但 REST API 不返回 Greeks。

**解决方案**：

```tsx
import React, {{ useEffect, useState }} from "react";
import {{
  getInstruments,
  getExpirations,
  groupByExpiry,
  market,
  Instrument
}} from "./lib/market";

interface OptionWithGreeks {{
  instrument: string;
  expiry: number;
  strike: number;
  type: "call" | "put";
  delta: number;
  iv: number;
}}

export default function TermStructure() {{
  const [expirations, setExpirations] = useState<any[]>([]);
  const [optionsData, setOptionsData] = useState<Map<string, OptionWithGreeks>>(new Map());
  const [deltaFilter, setDeltaFilter] = useState<string>("atm"); // atm, 25d-call, 25d-put, etc.

  useEffect(() => {{
    async function init() {{
      // 1. 获取所有到期日
      const expiries = await getExpirations("BTC");
      setExpirations(expiries);

      // 2. 获取所有期权合约
      const instruments = await getInstruments("BTC", "option");

      // 3. 订阅所有期权的 ticker 获取 Greeks
      const channels = instruments.map(i => `ticker.${{i.instrument_name}}.100ms`);

      const handler = (msg: any) => {{
        const data = msg?.params?.data;
        if (!data || !data.greeks) return;

        setOptionsData(prev => {{
          const next = new Map(prev);
          next.set(data.instrument_name, {{
            instrument: data.instrument_name,
            expiry: data.expiration_timestamp || 0,
            strike: data.strike || 0,
            type: data.instrument_name.endsWith("-C") ? "call" : "put",
            delta: data.greeks.delta,
            iv: data.mark_iv,
          }});
          return next;
        }});
      }};

      market.subscribe(channels, handler);
      return () => market.unsubscribe(channels, handler);
    }}

    init();
  }}, []);

  // 按 delta 筛选每个到期日的期权
  function getIvForExpiry(expiryTs: number): number | null {{
    const options = Array.from(optionsData.values()).filter(o =>
      Math.abs(o.expiry - expiryTs) < 86400000 // 同一天
    );

    let targetDelta: number;
    let isCall: boolean;

    switch (deltaFilter) {{
      case "atm":
        // ATM: delta ≈ ±0.5
        const atm = options.find(o => Math.abs(Math.abs(o.delta) - 0.5) < 0.1);
        return atm?.iv || null;
      case "25d-call":
        targetDelta = 0.25;
        isCall = true;
        break;
      case "25d-put":
        targetDelta = -0.25;
        isCall = false;
        break;
      case "10d-call":
        targetDelta = 0.10;
        isCall = true;
        break;
      case "10d-put":
        targetDelta = -0.10;
        isCall = false;
        break;
      default:
        return null;
    }}

    const match = options
      .filter(o => o.type === (isCall ? "call" : "put"))
      .reduce((closest, o) => {{
        if (!closest) return o;
        return Math.abs(o.delta - targetDelta) < Math.abs(closest.delta - targetDelta) ? o : closest;
      }}, null as OptionWithGreeks | null);

    return match?.iv || null;
  }}

  // 渲染 Term Structure 图表...
}}
```

**Delta 筛选标准**：
| 类型 | Delta 值 | 含义 |
|-----|---------|------|
| ATM | ±0.50 | At-The-Money，平值期权 |
| 25D Call | +0.25 | 25 Delta Call，轻度虚值 |
| 25D Put | -0.25 | 25 Delta Put，轻度虚值 |
| 10D Call | +0.10 | 10 Delta Call，深度虚值 |
| 10D Put | -0.10 | 10 Delta Put，深度虚值 |

**Term Structure 数据结构**：

```typescript
interface TermStructurePoint {{
  expiry: string;           // "2025-12-26"
  daysToExpiry: number;     // 4
  atmIv: number;            // 47.31
  call25Iv?: number;        // 45.2
  put25Iv?: number;         // 49.8
  call10Iv?: number;        // 43.1
  put10Iv?: number;         // 52.3
}}
```

### ⭐ Volatility Smile 实现指南

Volatility Smile 显示同一到期日不同行权价的 IV 分布。

```typescript
interface SmilePoint {{
  strike: number;           // 100000
  moneyness: number;        // strike / underlying_price
  callIv: number;           // Call 的 IV
  putIv: number;            // Put 的 IV
  atmDistance: number;      // 距离 ATM 的百分比
}}

// X 轴：Strike 或 Moneyness 或 Delta
// Y 轴：IV (%)
```

### ⭐ Options Chain 实现指南

期权链的标准 T 型布局：

```
           CALLS                    PUTS
IV   Bid  Ask  Delta │ Strike │ Delta  Bid  Ask   IV
47.3  0.15 0.17  0.51│ 89000  │ -0.49  0.14 0.16  47.1
45.2  0.12 0.14  0.35│ 92000  │ -0.35  0.11 0.13  45.8
43.1  0.08 0.10  0.22│ 95000  │ -0.22  0.07 0.09  44.2
                     │ 97234 ← 当前价格
41.5  0.05 0.07  0.15│ 100000 │ -0.15  0.04 0.06  42.8
```

**数据组织**：

```typescript
interface OptionChainRow {{
  strike: number;
  call: {{
    instrument: string;
    bid: number;
    ask: number;
    iv: number;
    delta: number;
    volume: number;
    oi: number;
  }};
  put: {{
    // 同上
  }};
}}

// 按 strike 排序，当前价格高亮
```

---

## 第三部分：UI 设计规范

### 颜色规范

```css
/* 背景 */
--bg-primary: #0a0a0a;
--bg-secondary: #141414;
--bg-tertiary: #1a1a1a;

/* 文字 */
--text-primary: #ffffff;
--text-secondary: #a0a0a0;
--text-muted: #666666;

/* 涨跌 */
--color-up: #00c853;       /* 上涨/Call */
--color-down: #ff5252;     /* 下跌/Put */

/* 强调 */
--color-accent: #2196f3;   /* 主强调色 */
--color-warning: #ff9800;  /* 警告 */

/* ATM 高亮 */
--color-atm: #ffeb3b;      /* ATM 行权价高亮 */
```

### 数字格式化规范

```typescript
// 价格：根据大小自动精度
function formatPrice(price: number): string {{
  if (price >= 1000) return price.toLocaleString('en-US', {{maximumFractionDigits: 2}});
  if (price >= 1) return price.toFixed(4);
  return price.toFixed(6);  // 期权价格通常很小
}}

// IV：百分比，1位小数
function formatIV(iv: number): string {{
  return iv.toFixed(1) + '%';
}}

// Delta：2-3位小数
function formatDelta(delta: number): string {{
  return delta.toFixed(3);
}}

// 大数字：缩写
function formatLargeNumber(n: number): string {{
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(2) + 'K';
  return n.toString();
}}

// 时间戳
function formatTimestamp(ts: number): string {{
  return new Date(ts).toLocaleTimeString();
}}
```

### 字体规范

```css
/* 数字使用等宽字体 */
.mono {{
  font-family: 'JetBrains Mono', 'Fira Code', 'SF Mono', monospace;
  font-variant-numeric: tabular-nums;
}}
```

### 实时数据动画

```css
/* 价格变化闪烁 */
@keyframes flash-up {{
  0% {{ background-color: rgba(0, 200, 83, 0.3); }}
  100% {{ background-color: transparent; }}
}}

@keyframes flash-down {{
  0% {{ background-color: rgba(255, 82, 82, 0.3); }}
  100% {{ background-color: transparent; }}
}}

.price-up {{ animation: flash-up 0.5s ease-out; }}
.price-down {{ animation: flash-down 0.5s ease-out; }}
```

### ⭐ 图表交互规范（必须实现）

所有图表必须支持鼠标悬停显示详细数据，不能只是静态图片！

#### Tooltip 必要性

- **用户期望**：专业金融图表都支持 hover 查看数据点详情
- **数据密度**：期权数据复杂，图表无法展示所有信息，tooltip 是关键补充
- **分析需求**：用户需要精确数值，而非目测曲线

#### 各场景 Tooltip 内容

**1. Term Structure 图表**
```typescript
// 鼠标悬停在曲线点上时显示：
interface TermStructureTooltip {{
  expiry: string;         // "2025-12-26 (4d)"
  iv: number;             // "IV: 47.31%"
  deltaFilter: string;    // "ATM" 或 "25D Call"
  underlyingPrice: number; // "Underlying: $97,234"
}}
```

**2. Volatility Smile 图表**
```typescript
interface SmileTooltip {{
  strike: number;         // "Strike: $100,000"
  iv: number;             // "IV: 45.2%"
  delta: number;          // "Delta: 0.35"
  moneyness: string;      // "OTM 3.2%"
  optionType: string;     // "Call" 或 "Put"
}}
```

**3. Options Chain 表格**
```typescript
// 鼠标悬停在行上时高亮并显示：
interface ChainRowTooltip {{
  instrument: string;     // "BTC-26DEC25-100000-C"
  greeks: {{
    delta: number;
    gamma: number;
    vega: number;
    theta: number;
  }};
  volume24h: number;
  openInterest: number;
  lastTradeTime: string;
}}
```

**4. 价格/K线图表**
```typescript
interface PriceTooltip {{
  time: string;           // "14:32:05"
  price: number;          // "$97,234.50"
  change: string;         // "+2.3%"
  volume?: number;        // "Vol: 1.2M"
}}
```

#### Recharts Tooltip 实现示例

```tsx
import {{ LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer }} from 'recharts';

// 自定义 Tooltip 组件
const CustomTooltip = ({{ active, payload, label }}: any) => {{
  if (!active || !payload?.length) return null;

  const data = payload[0].payload;
  return (
    <div style={{{{
      background: '#1a1a1a',
      border: '1px solid #333',
      borderRadius: '4px',
      padding: '12px',
      fontSize: '12px',
      fontFamily: 'monospace'
    }}}}>
      <div style={{{{ color: '#fff', fontWeight: 'bold', marginBottom: '8px' }}}}>
        {{data.expiryLabel}}
      </div>
      <div style={{{{ color: '#2196f3' }}}}>IV: {{data.iv?.toFixed(2)}}%</div>
      <div style={{{{ color: '#a0a0a0' }}}}>Days: {{data.daysToExpiry}}</div>
      <div style={{{{ color: '#a0a0a0' }}}}>Strike: ${{data.strike?.toLocaleString()}}</div>
    </div>
  );
}};

// 图表组件
function TermStructureChart({{ data }}: {{ data: any[] }}) {{
  return (
    <ResponsiveContainer width="100%" height={{400}}>
      <LineChart data={{data}}>
        <XAxis dataKey="expiryLabel" stroke="#666" />
        <YAxis stroke="#666" domain={['auto', 'auto']} />
        <Tooltip content={{<CustomTooltip />}} />
        <Line
          type="monotone"
          dataKey="iv"
          stroke="#2196f3"
          strokeWidth={{2}}
          dot={{{{ fill: '#2196f3', strokeWidth: 2, r: 4 }}}}
          activeDot={{{{ r: 6, fill: '#fff', stroke: '#2196f3' }}}}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}}
```

#### 交互细节要求

1. **Tooltip 样式**：深色背景、等宽字体、与整体风格一致
2. **数据格式化**：价格带千分位、IV 带百分号、Delta 保留 3 位小数
3. **响应速度**：即时显示，无延迟
4. **位置智能**：自动避免超出图表边界
5. **高亮联动**：悬停时对应数据点要有视觉反馈（放大、颜色变化等）

#### 表格 Hover 效果

```css
/* 表格行悬停 */
.option-row:hover {{
  background: rgba(33, 150, 243, 0.1);
  cursor: pointer;
}}

/* 单元格数值悬停显示完整精度 */
.price-cell {{
  position: relative;
}}

.price-cell:hover::after {{
  content: attr(data-full-value);
  position: absolute;
  background: #1a1a1a;
  border: 1px solid #333;
  padding: 4px 8px;
  border-radius: 4px;
  white-space: nowrap;
  z-index: 100;
}}
```

---

## 第四部分：开发流程（必须遵循）

### Step 1: 需求分析
- 识别业务场景
- 确定数据需求
- 规划 UI 结构

### Step 2: 实现代码
- 修改 src/App.tsx
- 使用 market.subscribe() 获取实时数据
- 遵循 UI 规范

### Step 3: 编译验证
```bash
pnpm install && pnpm run build
```

### Step 4: 运行验证（关键！）

```bash
pnpm run screenshot BTC-PERPETUAL
```

这个脚本会：
1. 启动开发服务器
2. 打开浏览器访问 Widget
3. **直接监控 WebSocket 消息**（不是从截图猜数据）
4. 验证收到的真实数据
5. 截图保存

**验证脚本直接检查 WebSocket 数据**：
- ✅ WebSocket Connected - 连接是否建立
- ✅ Data Received - 是否收到消息
- ✅ Subscription Data - 是否有 ticker/book/trades 订阅数据
- ✅ Price Valid - 价格是否在合理范围（BTC: $10K-$500K）
- ✅ IV Valid - 隐含波动率是否合理（1%-500%）
- ✅ Greeks Valid - Delta 是否在 -1 到 1 之间
- ✅ Data Fresh - 数据时间戳是否是最近的（<60秒）
- ✅ Real-Time Updates - 是否收到多个不同时间戳的更新

**输出文件**：
- `screenshot-latest.png` - 最新截图
- `validation-report.json` - 包含所有 WebSocket 消息的详细报告

### Step 5: 审查验证结果

1. **查看验证输出**：脚本会打印验证结果
   ```
   [3/5] Waiting for WebSocket connection...
        ✅ WebSocket connected
   [4/5] Waiting for real-time data (10s)...
        Received 47 WebSocket messages
        ✅ Data received
        ✅ Found 45 subscription updates
        ✅ Price valid: $97,234
        ✅ Data fresh: 0.3s old
        ✅ Real-time updates confirmed (45 unique)

   ════════════════════════════════════════════════════════════
   🎉 VALIDATION PASSED
      Widget is receiving and displaying real-time data correctly
   ════════════════════════════════════════════════════════════
   ```

2. **用 Read 查看截图**：确认视觉效果
   ```
   Read screenshot-latest.png
   ```

3. **如果验证失败**：
   - ❌ "WebSocket failed to connect" → 检查 market.subscribe() 调用
   - ❌ "No WebSocket messages received" → WebSocket URL 可能错误
   - ❌ "No subscription data found" → 没有调用 market.subscribe()
   - ❌ "Price out of range" → 可能用了 mock 数据
   - ⚠️ "Same timestamp in all messages" → 可能是静态假数据

### Step 6: 修复并重新验证
如果验证失败或有警告，修复代码后重复 Step 3-5。

**常见问题排查**：
| 问题 | 可能原因 | 解决方案 |
|-----|---------|---------|
| 数据为空 | WebSocket 未连接 | 检查 market.subscribe() 调用 |
| 价格不合理 | 解析错误或 mock 数据 | 检查数据源是否正确 |
| 数据不变化 | 使用了静态数据 | 确保用 market.subscribe() 获取实时数据 |
| IV/Greeks 缺失 | 订阅了错误的频道 | 确保订阅 ticker 频道（有 Greeks） |

### Step 7: 最终构建
```bash
pnpm run build
```

---

## 第五部分：代码示例

### 示例 1: 基础 Ticker（带选择器）

**注意**：Widget 应该自己管理数据源，不依赖外部 URL 参数。如果需要让用户切换合约，在 Widget 内部实现选择器。

```tsx
import React, {{ useEffect, useState, useRef }} from "react";
import {{ market }} from "./lib/market";

export default function App() {{
  // Widget 内部管理当前选中的合约
  const [instrument, setInstrument] = useState("BTC-PERPETUAL");
  const [data, setData] = useState<any>(null);
  const [priceDirection, setPriceDirection] = useState<'up' | 'down' | null>(null);
  const prevPrice = useRef<number | null>(null);

  useEffect(() => {{
    const ch = `ticker.${{instrument}}.100ms`;
    const handler = (msg: any) => {{
      const newData = msg?.params?.data;
      if (newData) {{
        if (prevPrice.current !== null && newData.last_price !== prevPrice.current) {{
          setPriceDirection(newData.last_price > prevPrice.current ? 'up' : 'down');
          setTimeout(() => setPriceDirection(null), 500);
        }}
        prevPrice.current = newData.last_price;
        setData(newData);
      }}
    }};
    market.subscribe([ch], handler);
    return () => market.unsubscribe([ch], handler);
  }}, [instrument]);

  if (!data) {{
    return (
      <div style={{{{ backgroundColor: "#0a0a0a", color: "#fff", padding: 16, minHeight: "100vh" }}}}>
        <div style={{{{ color: "#666" }}}}>Connecting to {{instrument}}...</div>
      </div>
    );
  }}

  const isOption = data.greeks !== undefined;
  const priceColor = priceDirection === 'up' ? '#00c853' : priceDirection === 'down' ? '#ff5252' : '#fff';

  return (
    <div style={{{{ backgroundColor: "#0a0a0a", color: "#fff", padding: 16, fontFamily: "system-ui" }}}}>
      {{/* 内置合约选择器 */}}
      <select
        value={{instrument}}
        onChange={{(e) => setInstrument(e.target.value)}}
        style={{{{ background: "#1a1a1a", color: "#fff", border: "1px solid #333", padding: "4px 8px", marginBottom: 12 }}}}
      >
        <option value="BTC-PERPETUAL">BTC-PERPETUAL</option>
        <option value="ETH-PERPETUAL">ETH-PERPETUAL</option>
      </select>

      <div style={{{{ fontSize: 32, fontFamily: "monospace", color: priceColor }}}}>
        ${{data.last_price?.toLocaleString()}}
      </div>

      {{isOption && data.greeks && (
        <div style={{{{ marginTop: 16, display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8 }}}}>
          <div><span style={{{{color: "#666"}}}}>Δ</span> {{data.greeks.delta?.toFixed(3)}}</div>
          <div><span style={{{{color: "#666"}}}}>Γ</span> {{data.greeks.gamma?.toFixed(5)}}</div>
          <div><span style={{{{color: "#666"}}}}>V</span> {{data.greeks.vega?.toFixed(2)}}</div>
          <div><span style={{{{color: "#666"}}}}>Θ</span> {{data.greeks.theta?.toFixed(2)}}</div>
          <div><span style={{{{color: "#666"}}}}>IV</span> {{data.mark_iv?.toFixed(1)}}%</div>
        </div>
      )}}
    </div>
  );
}}
```

### 示例 2: 多期权订阅（用于 Term Structure）

```tsx
import React, {{ useEffect, useState }} from "react";
import {{ market }} from "./lib/market";

interface OptionData {{
  instrument: string;
  expiry: number;
  strike: number;
  type: 'call' | 'put';
  delta: number;
  iv: number;
}}

export default function App() {{
  const [options, setOptions] = useState<Map<string, OptionData>>(new Map());

  useEffect(() => {{
    // 假设已通过 MCP 获取了期权列表
    const instruments = [
      "BTC-26DEC25-90000-C",
      "BTC-26DEC25-95000-C",
      "BTC-26DEC25-100000-C",
      // ... 更多
    ];

    const channels = instruments.map(i => `ticker.${{i}}.100ms`);

    const handler = (msg: any) => {{
      const data = msg?.params?.data;
      if (!data) return;

      setOptions(prev => {{
        const next = new Map(prev);
        next.set(data.instrument_name, {{
          instrument: data.instrument_name,
          expiry: parseExpiry(data.instrument_name),
          strike: parseStrike(data.instrument_name),
          type: data.instrument_name.endsWith('-C') ? 'call' : 'put',
          delta: data.greeks?.delta || 0,
          iv: data.mark_iv || 0,
        }});
        return next;
      }});
    }};

    market.subscribe(channels, handler);
    return () => market.unsubscribe(channels, handler);
  }}, []);

  // ... 渲染 Term Structure 图表
}}

function parseExpiry(name: string): number {{
  // BTC-26DEC25-100000-C -> 提取 26DEC25 -> 转换为时间戳
  const match = name.match(/-(\\d{{2}})([A-Z]{{3}})(\\d{{2}})-/);
  if (!match) return 0;
  const [, day, mon, year] = match;
  const months: Record<string, number> = {{JAN:0,FEB:1,MAR:2,APR:3,MAY:4,JUN:5,JUL:6,AUG:7,SEP:8,OCT:9,NOV:10,DEC:11}};
  return new Date(2000 + parseInt(year), months[mon], parseInt(day)).getTime();
}}

function parseStrike(name: string): number {{
  const match = name.match(/-(\\d+)-[CP]$/);
  return match ? parseInt(match[1]) : 0;
}}
```

---

## 约束条件

1. **必须使用真实数据** - 禁止 mock，禁止硬编码假数据
2. **必须通过 market.ts** - 不要直接调用 Deribit API
3. **最小化依赖** - 优先使用已有依赖
4. **响应式设计** - 适配 iframe 嵌入
5. **专业视觉** - 遵循金融终端设计规范
6. **目标尺寸** - Widget 将在 **1152px x 500px** 的 iframe 中显示，请确保：
   - 布局适合这个宽高比（约 2.3:1 横向布局）
   - 不要设计需要滚动才能看完的内容
   - 图表高度建议 350-450px，留出标题和控件空间
   - 如果内容较多，使用标签页或折叠面板而非滚动
""", encoding="utf-8")

        log("[2/6] CLAUDE.md written")

        # 配置 MCP server（可选，忽略失败）
        log("[3/6] Configuring MCP server...")
        try:
            _run(["claude", "mcp", "add", "--transport", "http", "deribit", MCP_URL], cwd=ws_dir)
            log("    MCP server configured")
        except Exception as e:
            log(f"    MCP config skipped: {e}")

        prompt = f"""你是一个专业的 Deribit 期权/期货数据可视化专家。请根据用户需求开发一个专业级的 Widget。

## 用户需求
{user_prompt}

---

## 第一步：需求分析（必须先做！）

在写任何代码之前，你必须：

1. **识别业务场景**：用户想要的是什么类型的分析？
   - 期权链 (Options Chain)
   - 期限结构 (Term Structure)
   - 波动率微笑 (Volatility Smile)
   - Greeks 仪表盘
   - 期权流 (Options Flow)
   - 永续合约行情
   - 订单簿深度
   - 还是其他？

2. **分析真实需求**：用户字面说的和实际想要的可能不同
   - 例如：用户说"做个 Term Structure"，实际需要的是各到期日的 IV 曲线，且应该支持按 Delta 切换（ATM, 25D, 10D 等）
   - 例如：用户说"看期权"，可能想要的是完整的期权链视图

3. **确定数据需求**：
   - 需要哪些数据字段？
   - 需要订阅哪些 WebSocket 频道？
   - 是否需要 Greeks？（只有 ticker 频道有）
   - 是否需要批量数据？（用 MCP 工具）

4. **规划产品方案**：
   - UI 应该长什么样？
   - 需要什么交互功能？
   - 专业的金融终端是怎么做的？

请先在心里完成这个分析，然后再开始写代码。

---

## 第二步：阅读开发指南

**必须先阅读 CLAUDE.md**，里面包含：
- Deribit 数据结构详解（期权 ticker、Greeks、trades 等）
- 各种业务场景的实现指南
- UI 设计规范
- 代码示例

---

## 第三步：实现

1. 修改 `src/App.tsx` 实现 Widget
2. 使用 `market.subscribe()` 获取实时数据
3. 遵循 CLAUDE.md 中的 UI 规范

---

## 第四步：验证（极其重要！）

### 编译
```bash
pnpm install && pnpm run build
```

### 截图验证
```bash
pnpm run screenshot
```

### 审查截图
用 Read 工具查看 `screenshot-latest.png`，**必须确认**：

1. **真实数据验证**（最重要！）
   - [ ] 显示的是真实的市场数据，不是 mock/placeholder/硬编码
   - [ ] 价格、IV、Greeks 等数值看起来合理（BTC 价格应该在合理范围内，IV 应该是正常百分比）
   - [ ] 如果可能，截第二张图确认数据在变化

2. **专业性验证**
   - [ ] 符合用户的真实需求，不只是字面需求
   - [ ] 符合专业金融终端的视觉标准
   - [ ] 数字格式正确（价格、百分比、Greeks）

3. **视觉验证**
   - [ ] 深色主题正确应用
   - [ ] 布局清晰、信息层次分明
   - [ ] 没有 UI 错误或空白区域

如果任何一项不通过，修复后重新截图验证。

---

## 第五步：最终构建

确认截图无误后：
```bash
pnpm run build
```

---

## ⚠️ 关键约束

1. **禁止 Mock 数据** - 必须连接真实 WebSocket，显示真实市场数据
2. **禁止直接调 Deribit API** - 必须通过 src/lib/market.ts
3. **必须验证数据真实性** - 截图中的数据必须是真实的、合理的
4. **最小化依赖** - 优先使用已有依赖
5. **专业标准** - 输出应该达到专业金融终端的水平
"""

        log("[4/6] Running Claude Code to generate widget...")

        # 生成 session_id 用于后续多轮对话
        session_id = str(uuid.uuid4())
        session_id = _run_claude(prompt, ws_dir, log, session_id=session_id, resume=False)

        log("[5/6] Installing dependencies and building...")

        build_env = os.environ.copy()

        # 安装依赖
        install_result = subprocess.run(
            ["pnpm", "install"],
            cwd=str(ws_dir),
            env=build_env,
            capture_output=True,
            text=True
        )
        log(f"    pnpm install exit: {install_result.returncode}")

        # 构建
        build_result = subprocess.run(
            ["pnpm", "run", "build"],
            cwd=str(ws_dir),
            env=build_env,
            capture_output=True,
            text=True
        )
        log(f"    pnpm build exit: {build_result.returncode}")
        if build_result.stderr:
            log(f"    Build stderr: {build_result.stderr[:500]}...")

        log("[6/6] Creating dist symlink...")

        # Vite dist 输出 - create symlink for URL access
        built = ws_dir / "dist"
        dist_link = out_root / "dist"

        # Remove old dist link/folder if exists
        if dist_link.is_symlink() or dist_link.exists():
            if dist_link.is_symlink():
                dist_link.unlink()
            else:
                shutil.rmtree(dist_link)

        if built.exists():
            # Create symlink: widget_id/dist -> widget_id/workspace/dist
            dist_link.symlink_to("workspace/dist")
            log("    Created symlink dist -> workspace/dist")
        else:
            log("    WARNING: No dist folder found!")

        # 标记完成
        meta_path = out_root / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            meta = {"id": widget_id, "prompt": user_prompt}
        meta["status"] = "ready" if built.exists() else "failed"
        meta["session_id"] = session_id  # 保存 session_id 用于多轮对话
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        log("Build completed!")

    except Exception as e:
        log(f"ERROR: {str(e)}")
        # 更新状态为失败
        meta_path = out_root / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            meta = {"id": widget_id, "prompt": user_prompt}
        meta["status"] = "failed"
        meta["error"] = str(e)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def chat_widget(widget_id: str, message: str, session_id: str | None, widgets_dir: str):
    """Continue conversation with existing widget to modify it."""
    out_root = Path(widgets_dir) / widget_id
    ws_dir = out_root / "workspace"
    log_path = out_root / "build.log"

    logs = []

    def log(msg: str):
        logs.append(msg)
        log_path.write_text("\n".join(logs), encoding="utf-8")

    try:
        log(f"[Chat] Continuing conversation for widget {widget_id}...")
        log(f"[Chat] User message: {message}")

        if not ws_dir.exists():
            log("ERROR: Workspace not found!")
            raise Exception("Workspace not found")

        # 读取 meta 获取 session_id
        meta_path = out_root / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not session_id:
                session_id = meta.get("session_id")
        else:
            meta = {"id": widget_id}

        log(f"[Chat] Session ID: {session_id or 'None (new session)'}")

        # 构建 prompt
        prompt = f"""用户要求修改这个 Widget：

## 修改需求
{message}

---

## 修改流程

### 1. 理解需求
先分析用户的修改需求：
- 是功能调整还是 UI 调整？
- 是否需要新的数据源？
- 参考 CLAUDE.md 中的业务场景和数据指南

### 2. 修改代码
- 修改 src/App.tsx
- 如需新数据，确保正确使用 market.subscribe()
- 遵循 CLAUDE.md 中的 UI 规范

### 3. 验证

```bash
pnpm run build
pnpm run screenshot
```

用 Read 工具查看 `screenshot-latest.png`，确认：
- [ ] 修改效果符合预期
- [ ] 显示的是**真实数据**（不是 mock）
- [ ] 数据看起来合理（价格、IV、Greeks 等）
- [ ] 没有引入新的问题

### 4. 修复并重新验证
如有问题，修复后重复步骤 3。

### 5. 最终构建
```bash
pnpm run build
```

---

## ⚠️ 关键提醒
- 必须使用真实数据，禁止 mock
- 必须通过截图验证修改效果
- 数据必须是从 WebSocket 实时获取的
"""

        log("[Chat] Running Claude Code...")

        # 使用 resume 继续对话
        new_session_id = _run_claude(prompt, ws_dir, log, session_id=session_id, resume=bool(session_id))

        log("[Chat] Installing dependencies and building...")

        env = os.environ.copy()

        # 安装依赖
        install_result = subprocess.run(
            ["pnpm", "install"],
            cwd=str(ws_dir),
            env=env,
            capture_output=True,
            text=True
        )
        log(f"    pnpm install exit: {install_result.returncode}")

        # 构建
        build_result = subprocess.run(
            ["pnpm", "run", "build"],
            cwd=str(ws_dir),
            env=env,
            capture_output=True,
            text=True
        )
        log(f"    pnpm build exit: {build_result.returncode}")
        if build_result.stderr:
            log(f"    Build stderr: {build_result.stderr[:500]}...")

        log("[Chat] Creating dist symlink...")

        # Vite dist 输出 - create symlink for URL access
        built = ws_dir / "dist"
        dist_link = out_root / "dist"

        # Remove old dist link/folder if exists
        if dist_link.is_symlink() or dist_link.exists():
            if dist_link.is_symlink():
                dist_link.unlink()
            else:
                shutil.rmtree(dist_link)

        if built.exists():
            # Create symlink: widget_id/dist -> widget_id/workspace/dist
            dist_link.symlink_to("workspace/dist")
            log("    Created symlink dist -> workspace/dist")
        else:
            log("    WARNING: No dist folder found!")

        # 更新 meta
        meta["status"] = "ready" if built.exists() else "failed"
        meta["session_id"] = new_session_id or session_id
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        log("[Chat] Modification completed!")

    except Exception as e:
        log(f"ERROR: {str(e)}")
        meta_path = out_root / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            meta = {"id": widget_id}
        meta["status"] = "failed"
        meta["error"] = str(e)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

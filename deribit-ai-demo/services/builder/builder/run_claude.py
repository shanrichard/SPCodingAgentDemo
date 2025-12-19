import os
import shutil
import subprocess
import json
import uuid
from pathlib import Path

TEMPLATE_DIR = Path("/app/template/widget-template")
MCP_URL = os.getenv("MCP_URL", "http://mcp:7001/mcp")


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
        bufsize=0
    )

    # 实时解析 JSON 事件流
    result_session_id = session_id
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

    proc.wait(timeout=300)
    log_func(f"    Claude exit code: {proc.returncode}")

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


def build_widget(widget_id: str, user_prompt: str, instrument: str, widgets_dir: str):
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

        # 给 Claude 的项目说明（关键：让它"自由但不乱跑"）
        claude_md = ws_dir / "CLAUDE.md"
        claude_md.write_text(f"""# Widget Project Rules

You are editing a self-contained widget (React + Vite + TypeScript).
Goal: implement the requested widget UI for Deribit market data.

## IMPORTANT: Development Workflow (MUST FOLLOW)

You MUST follow this workflow for EVERY widget:

### Step 1: Implement the widget
- Modify src/App.tsx to implement the requested UI
- Use market.subscribe() from src/lib/market.ts for real-time data
- Use dark theme (dark background #0a0a0a, light text)

### Step 2: Build and verify compilation
```bash
pnpm install
pnpm run build
```
Fix any TypeScript errors before proceeding.

### Step 3: Visual validation with screenshot
```bash
pnpm run screenshot
```
This starts dev server, takes a screenshot, saves to `screenshot-YYYYMMDD-HHMMSS.png` and `screenshot-latest.png`.
Note: Your widget should work with any instrument via ?instrument= query param, or use aggregated channels like `trades.option.BTC.100ms` for multi-instrument data.

### Step 4: Review the screenshot
Use the Read tool to view `screenshot-latest.png` and verify:
- [ ] Dark theme is applied (dark background, light text)
- [ ] All requested data is displayed
- [ ] Layout looks correct and readable
- [ ] No broken UI elements

### Step 5: Fix issues if any
If the screenshot shows problems:
1. Fix the code
2. Re-run `pnpm run build`
3. Re-run `pnpm run screenshot`
4. Review again

### Step 6: Final build
Once screenshot looks correct:
```bash
pnpm run build
```

## Constraints
- Do NOT fetch Deribit directly from browser. Use src/lib/market.ts (connects to /ws/market).
- Keep dependencies minimal. Prefer existing deps.
- For single-instrument widgets: read instrument from query param ?instrument=...
- For multi-instrument widgets (like options trades): use aggregated channels (e.g., trades.option.BTC.100ms)
- The widget should fit in an iframe and be responsive.

## Data subscription
Use market.subscribe([...channels]) where channels follow Deribit public channels.

### Ticker channels (interval: 100ms, agg2, raw)
- ticker.<instrument>.100ms - real-time ticker (perpetual, futures, options, spot)
- incremental_ticker.<instrument> - incremental updates

### Order book channels
- book.<instrument>.100ms - full orderbook snapshot
- book.<instrument>.<group>.<depth>.<interval> - grouped depth (group: none/1/2/5/10)

### Trade channels
- trades.<instrument>.100ms - instrument trades
- trades.<kind>.<currency>.100ms - aggregated by type (kind: future/option/spot)

### K-line / Chart
- chart.trades.<instrument>.<resolution> - OHLC (resolution: 1/3/5/10/15/30/60/120/180/360/720/1D)

### Index & Volatility
- deribit_price_index.<index> - price index (btc_usd, eth_usd, sol_usd, etc.)
- deribit_volatility_index.<index> - volatility index (btc_usd, eth_usd)
- deribit_price_statistics.<index> - price statistics
- estimated_expiration_price.<index> - expiration estimates

### Options specific
- markprice.options.<index> - options mark prices

### Instrument state
- instrument.state.<kind>.<currency> - state changes (kind: future/option/spot/combo)

## Instrument types
- Perpetuals: e.g. BTC-PERPETUAL, ETH-PERPETUAL, SOL-PERPETUAL
- Futures: BTC-27DEC24, ETH-28MAR25, etc.
- Options: BTC-27DEC24-100000-C (Call), BTC-27DEC24-90000-P (Put)
- Spot: BTC_USDC, ETH_USDC, etc.

## Available ticker data fields

### Common fields (all instruments)
- last_price, mark_price, index_price
- best_bid_price, best_ask_price, best_bid_amount, best_ask_amount
- volume_usd, volume_notional, price_change
- high, low (24h)
- timestamp

### Perpetual/Futures specific
- funding_8h, current_funding, interest_rate
- open_interest, settlement_price
- estimated_delivery_price

### Options specific
- underlying_price, underlying_index
- mark_iv, bid_iv, ask_iv (implied volatility)
- greeks: delta, gamma, vega, theta, rho

## Example App.tsx structure
```tsx
import React, {{ useEffect, useState }} from "react";
import {{ market }} from "./lib/market";

export default function App() {{
  const instrument = new URLSearchParams(location.search).get("instrument") || "{instrument}";
  const [data, setData] = useState<any>(null);

  useEffect(() => {{
    const ch = `ticker.${{instrument}}.100ms`;
    const handler = (msg: any) => {{
      setData(msg?.params?.data);
    }};
    market.subscribe([ch], handler);
    return () => market.unsubscribe([ch], handler);
  }}, [instrument]);

  return (
    <div style={{{{ backgroundColor: "#0a0a0a", color: "#fff", padding: 16 }}}}>
      {{{{/* Your UI here */}}}}
    </div>
  );
}}
```
""", encoding="utf-8")

        log("[2/6] CLAUDE.md written")

        # 配置 MCP server（可选，忽略失败）
        log("[3/6] Configuring MCP server...")
        try:
            _run(["claude", "mcp", "add", "--transport", "http", "deribit", MCP_URL], cwd=ws_dir)
            log("    MCP server configured")
        except Exception as e:
            log(f"    MCP config skipped: {e}")

        prompt = f"""Build a widget per this request:

User request:
{user_prompt}

## Data Source Selection:
- For single-instrument widgets (ticker, orderbook): use query param ?instrument=...
- For multi-instrument widgets (e.g., "all BTC options trades"): use aggregated channels like `trades.option.BTC.100ms`
- Choose the appropriate data source based on the user's request!

## CRITICAL: You MUST follow the workflow in CLAUDE.md exactly!

### Required steps (DO NOT SKIP):
1. **Read CLAUDE.md first** - understand the project and workflow
2. **Implement the widget** in src/App.tsx
3. **Build**: `pnpm install && pnpm run build`
4. **Take screenshot**: `pnpm run screenshot`
5. **Review screenshot-latest.png** - use Read tool to view it and verify:
   - Dark theme applied?
   - All requested data displayed?
   - Layout correct?
6. **Fix and re-screenshot** if needed
7. **Final build**: `pnpm run build`

The screenshot validation step is MANDATORY. You must view screenshot-latest.png
and confirm the widget looks correct before finishing.

Do NOT install additional packages unless absolutely necessary.
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
            meta = {"id": widget_id, "prompt": user_prompt, "instrument": instrument}
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
            meta = {"id": widget_id, "prompt": user_prompt, "instrument": instrument}
        meta["status"] = "failed"
        meta["error"] = str(e)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def chat_widget(widget_id: str, message: str, session_id: str | None, instrument: str, widgets_dir: str):
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
            meta = {"id": widget_id, "instrument": instrument}

        log(f"[Chat] Session ID: {session_id or 'None (new session)'}")

        # 构建 prompt
        prompt = f"""用户要求修改这个 widget：

{message}

请根据用户要求修改代码，然后：
1. 修改 src/App.tsx
2. 运行 `pnpm run build` 确保编译通过
3. 运行 `pnpm run screenshot` 截图验证
4. 查看 screenshot.png 确认修改效果
5. 如有问题继续修复
6. 最终 `pnpm run build`
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
            meta = {"id": widget_id, "instrument": instrument}
        meta["status"] = "failed"
        meta["error"] = str(e)
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

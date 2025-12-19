# SPCodingAgent Demo - 项目进度跟踪

## 项目概述
AI 驱动的实时数据可视化 Widget 生成系统，让 Claude Code 根据自然语言需求自动生成连接 Deribit 实时数据的 Widget。

---

## 进度总览

| 阶段 | 状态 | 说明 |
|------|------|------|
| 1. 项目目录结构 | [x] | 创建所有服务目录和文件结构 |
| 2. API 服务 | [x] | FastAPI + WebSocket 网关 + 静态托管 |
| 3. Builder 服务 | [x] | 调用 Claude Code headless 生成代码 |
| 4. MCP 服务 | [x] | 为 Claude 提供 Deribit 工具 |
| 5. Widget 模板 | [x] | React + Vite 模板工程 |
| 6. Docker Compose | [x] | 容器编排配置 |
| 7. Demo 页面 | [x] | 前端输入界面 + iframe 展示 |
| 8. 测试运行 | [ ] | 端到端验证 |

---

## 详细任务清单

### 1. 项目目录结构
- [x] 创建 `services/api/` 目录
- [x] 创建 `services/builder/` 目录
- [x] 创建 `services/mcp/` 目录
- [x] 创建 `data/widgets/` 目录（共享卷）

### 2. API 服务 (`services/api/`)
- [x] `Dockerfile`
- [x] `requirements.txt`
- [x] `app/main.py` - FastAPI 入口
- [x] `app/deribit_hub.py` - WebSocket 网关（单连接扇出 + 心跳）
- [x] `app/widgets.py` - Widget 创建 API
- [x] `static/index.html` - Demo 页面

**关键功能：**
- `/` - Demo 页面
- `/api/widgets` - POST 创建 Widget
- `/ws/market` - WebSocket 实时数据订阅
- `/widgets/<id>/` - 静态 Widget 托管

### 3. Builder 服务 (`services/builder/`)
- [x] `Dockerfile` (Node + Python + Claude Code)
- [x] `requirements.txt`
- [x] `builder/main.py` - FastAPI 接收构建任务
- [x] `builder/run_claude.py` - 调用 Claude Code + pnpm build

**关键功能：**
- 接收构建请求
- 复制模板到 workspace
- 生成 CLAUDE.md 约束文件
- 调用 `claude -p` headless 模式
- 执行 `pnpm install && pnpm build`

### 4. MCP 服务 (`services/mcp/`)
- [x] `Dockerfile`
- [x] `requirements.txt`
- [x] `mcp_server.py` - FastMCP 服务

**提供的工具：**
- `list_instruments()` - 列出 Deribit 交易对
- `channel_cheatsheet()` - 频道参数速查
- `get_ticker_fields()` - Ticker 数据字段速查

### 5. Widget 模板 (`services/builder/template/widget-template/`)
- [x] `package.json`
- [x] `vite.config.ts`
- [x] `tsconfig.json`
- [x] `index.html`
- [x] `src/main.tsx`
- [x] `src/App.tsx` - 初始模板
- [x] `src/lib/market.ts` - WebSocket 订阅 SDK

### 6. Docker Compose
- [x] `docker-compose.yml`
- [x] `.env.example`
- [x] `.gitignore`

**服务依赖关系：**
```
api (8080) → builder (8090) → mcp (7001)
```

### 7. Demo 页面
- [x] 输入框（prompt + instrument）
- [x] Generate 按钮
- [x] iframe 展示区域
- [x] 状态显示
- [x] 示例 prompt 快捷按钮

### 8. 测试运行
- [ ] 配置 `.env` 文件（填入 ANTHROPIC_API_KEY）
- [ ] `docker-compose up --build`
- [ ] 访问 http://localhost:8080/
- [ ] 测试 prompt: "做一个 BTC-PERPETUAL 的实时行情卡片"
- [ ] 验证 iframe 展示实时数据

---

## 技术要点备忘

### Deribit WebSocket Endpoints
- **Prod**: `wss://streams.deribit.com/ws/api/v2`
- **Test**: `wss://test.deribit.com/den/ws`

### 常用频道格式
- Ticker: `ticker.<instrument>.100ms`
- Book: `book.<instrument>.100ms`
- Trades: `trades.<instrument>.100ms`
- Chart: `chart.trades.<instrument>.<resolution>`

### 端口分配
| 服务 | 端口 |
|------|------|
| API | 8080 |
| Builder | 8090 |
| MCP | 7001 |

### 安全白名单（频道前缀）
- `ticker.`
- `book.`
- `trades.`
- `chart.trades.`
- `deribit_price_index.`

---

## 已完成记录

- **2025-12-19**: 项目骨架搭建完成
  - 创建三层服务架构：API / Builder / MCP
  - 创建 Widget 模板工程（React + Vite + TypeScript）
  - 创建 Docker Compose 配置
  - 创建 Demo 页面（深色主题 + 示例 prompt）

---

## 问题与解决

（遇到问题时在此记录）

---

## 启动命令

```bash
# 1. 进入项目目录
cd /home/richard/SPCodingAgentDemo/deribit-ai-demo

# 2. 复制环境配置并填入 API Key
cp .env.example .env
# 编辑 .env，填入 ANTHROPIC_API_KEY

# 3. 启动服务
docker-compose up --build

# 4. 访问 Demo 页面
# http://localhost:8080/
```

---

*最后更新: 2025-12-19*

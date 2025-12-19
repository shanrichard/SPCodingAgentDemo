from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .deribit_hub import MarketHub
from .widgets import router as widgets_router

app = FastAPI(title="Deribit AI Demo")

hub = MarketHub()


@app.on_event("startup")
async def _startup():
    await hub.start()


@app.on_event("shutdown")
async def _shutdown():
    await hub.stop()


@app.get("/")
def index():
    return FileResponse("static/index.html")


# demo 生成的 widget 静态资源
# /widgets/<id>/index.html 由 Vite build 输出
app.mount("/widgets", StaticFiles(directory="/data/widgets", html=True), name="widgets")

# 业务 API：创建 widget、查询状态等
app.include_router(widgets_router, prefix="/api")

# WebSocket：浏览器 widget 通过它订阅实时行情
app.add_api_websocket_route("/ws/market", hub.ws_handler)

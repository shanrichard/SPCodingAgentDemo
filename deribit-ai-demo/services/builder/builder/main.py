import os
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from .run_claude import build_widget, chat_widget

WIDGETS_DIR = os.getenv("WIDGETS_DIR", "/data/widgets")

app = FastAPI(title="Widget Builder")


class BuildReq(BaseModel):
    id: str
    prompt: str


class ChatReq(BaseModel):
    id: str
    message: str
    session_id: str | None = None


@app.post("/build")
def build(req: BuildReq, background_tasks: BackgroundTasks):
    # 使用后台任务执行构建，避免请求超时
    background_tasks.add_task(build_widget, req.id, req.prompt, WIDGETS_DIR)
    return {"ok": True, "id": req.id}


@app.post("/chat")
def chat(req: ChatReq, background_tasks: BackgroundTasks):
    # 继续对话修改 widget
    background_tasks.add_task(chat_widget, req.id, req.message, req.session_id, WIDGETS_DIR)
    return {"ok": True, "id": req.id}


@app.get("/health")
def health():
    return {"status": "ok"}

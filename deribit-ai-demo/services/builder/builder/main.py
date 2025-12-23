import os
import signal
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from .run_claude import build_widget, chat_widget

WIDGETS_DIR = os.getenv("WIDGETS_DIR", "/data/widgets")


def _reap_children():
    """Reap any zombie child processes to prevent accumulation.

    This is necessary because Claude Code spawns subprocesses (esbuild, chrome, etc.)
    which may be orphaned and reparented to this process. Without explicit reaping,
    they become zombies.
    """
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
        except ChildProcessError:
            # No more children to reap
            break


async def _periodic_reaper():
    """Periodically reap zombie children in the background."""
    while True:
        _reap_children()
        await asyncio.sleep(5)  # Reap every 5 seconds


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - setup signal handler and periodic reaper."""
    # Set up SIGCHLD handler to auto-reap children
    original_handler = signal.getsignal(signal.SIGCHLD)

    def sigchld_handler(signum, frame):
        _reap_children()
        # Call original handler if it was a callable
        if callable(original_handler) and original_handler not in (signal.SIG_DFL, signal.SIG_IGN):
            original_handler(signum, frame)

    signal.signal(signal.SIGCHLD, sigchld_handler)

    # Start periodic reaper as a backup
    reaper_task = asyncio.create_task(_periodic_reaper())

    yield

    # Cleanup
    reaper_task.cancel()
    try:
        await reaper_task
    except asyncio.CancelledError:
        pass

    # Restore original handler
    signal.signal(signal.SIGCHLD, original_handler if original_handler else signal.SIG_DFL)


app = FastAPI(title="Widget Builder", lifespan=lifespan)


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

import time
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from app import process_message


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Megatron API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# REQUEST MODEL
# ============================================================

class ChatRequest(BaseModel):
    message: str


# ============================================================
# HEALTH
# ============================================================

@app.get("/")
def root():
    return {
        "service": "Megatron API",
        "status": "online",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
    }


# ============================================================
# CHAT
# ============================================================

@app.post("/chat")
def chat(request: ChatRequest):

    message = str(request.message).strip()

    if not message:
        return {
            "success": False,
            "error": "Message cannot be empty.",
        }

    started = time.perf_counter()

    try:
        reply = process_message(message)

        elapsed = time.perf_counter() - started

        print(
            f"[TIMING] api_chat={elapsed:.3f}s"
        )

        return {
            "success": True,
            "reply": str(reply),
            "latency_seconds": round(elapsed, 3),
        }

    except Exception as error:

        print(
            "[API ERROR]",
            error,
        )

        return {
            "success": False,
            "error": str(error),
        }


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

  uvicorn.run(
    app,
    host="0.0.0.0",
    port=int(os.getenv("PORT", "8000")),
)
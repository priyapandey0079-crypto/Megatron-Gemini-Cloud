import io
import os
import re
import time

import edge_tts

from fastapi import (
    FastAPI,
    File,
    UploadFile,
    HTTPException,
)

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from pydantic import BaseModel

from dotenv import load_dotenv
from groq import Groq

import uvicorn

from app import process_message


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

GROQ_API_KEY = os.getenv(
    "GROQ_API_KEY"
)

STT_MODEL = os.getenv(
    "GROQ_STT_MODEL",
    "whisper-large-v3-turbo"
)

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY not found."
    )

groq_client = Groq(
    api_key=GROQ_API_KEY
)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Megatron API",
    version="2.0.0",
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
# ROOT
# ============================================================

@app.get("/")
def root():
    return {
        "service": "Megatron API",
        "status": "online",
        "version": "2.0.0",
    }


# ============================================================
# HEALTH
# ============================================================

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

    message = str(
        request.message
    ).strip()

    if not message:
        return {
            "success": False,
            "error": "Message cannot be empty.",
        }

    started = time.perf_counter()

    try:

        reply = process_message(
            message
        )

        elapsed = (
            time.perf_counter()
            - started
        )

        print(
            f"[TIMING] api_chat={elapsed:.3f}s"
        )

        return {
            "success": True,
            "reply": str(reply),
            "latency_seconds": round(
                elapsed,
                3
            ),
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
# VOICE → TEXT → MEGATRON
# ============================================================

@app.post("/voice")
async def voice(
    audio: UploadFile = File(...)
):

    started = time.perf_counter()

    try:

        audio_bytes = await audio.read()

        if not audio_bytes:
            raise HTTPException(
                status_code=400,
                detail="Empty audio file.",
            )

        # Safety limit: about 1 MB
        if len(audio_bytes) > 1_000_000:
            raise HTTPException(
                status_code=413,
                detail="Audio file is too large.",
            )

        print(
            f"[VOICE] Received "
            f"{len(audio_bytes)} bytes"
        )

        # ----------------------------------------------------
        # GROQ WHISPER
        # ----------------------------------------------------

        transcription =
            groq_client.audio.transcriptions.create(
                file=(
                    "megatron.wav",
                    audio_bytes,
                    "audio/wav",
                ),
                model=STT_MODEL,
            )

        transcript = str(
            transcription.text
        ).strip()

        print(
            "[VOICE] Transcript:",
            transcript,
        )

        if not transcript:

            return {
                "success": False,
                "error": "No speech detected.",
            }

        # ----------------------------------------------------
        # EXISTING MEGATRON BRAIN
        # ----------------------------------------------------

        reply = process_message(
            transcript
        )

        elapsed = (
            time.perf_counter()
            - started
        )

        print(
            f"[TIMING] api_voice={elapsed:.3f}s"
        )

        return {
            "success": True,
            "transcript": transcript,
            "reply": str(reply),
            "latency_seconds": round(
                elapsed,
                3
            ),
        }

    except HTTPException:
        raise

    except Exception as error:

        print(
            "[VOICE ERROR]",
            error,
        )

        return {
            "success": False,
            "error": str(error),
        }


# ============================================================
# TEXT → SPEECH
# ============================================================

@app.get("/tts")
async def tts(text: str):

    text = str(
        text
    ).strip()

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Text cannot be empty.",
        )

    # Keep the voice request compact.
    text = text[:300]

    # Devanagari → Hindi neural voice
    # Otherwise use Indian English neural voice.
    if re.search(
        r"[\u0900-\u097F]",
        text
    ):
        voice = "hi-IN-SwaraNeural"
    else:
        voice = "en-IN-NeerjaNeural"

    print(
        f"[TTS] Voice: {voice}"
    )

    try:

        communicate = edge_tts.Communicate(
            text,
            voice,
        )

        audio_chunks = []

        async for chunk in communicate.stream():

            if (
                chunk["type"]
                == "audio"
            ):
                audio_chunks.append(
                    chunk["data"]
                )

        audio_data = b"".join(
            audio_chunks
        )

        if not audio_data:
            raise HTTPException(
                status_code=500,
                detail="TTS returned no audio.",
            )

        print(
            f"[TTS] Generated "
            f"{len(audio_data)} bytes"
        )

        return StreamingResponse(
            io.BytesIO(
                audio_data
            ),
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-store",
            },
        )

    except HTTPException:
        raise

    except Exception as error:

        print(
            "[TTS ERROR]",
            error,
        )

        raise HTTPException(
            status_code=500,
            detail=str(error),
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )

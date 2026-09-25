import io
import os
import re
import time
from urllib.parse import quote

import edge_tts
import uvicorn
from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from groq import Groq
from pydantic import BaseModel

from app import process_message


# ============================================================
# ENVIRONMENT
# ============================================================

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

STT_MODEL = os.getenv(
    "GROQ_STT_MODEL",
    "whisper-large-v3-turbo",
)

TTS_VOICE = os.getenv(
    "MEGATRON_TTS_VOICE",
    "en-US-AriaNeural",
)


if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY not found in environment variables."
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
# HELPERS
# ============================================================

def clean_tts_text(text: str) -> str:
    """
    Remove common markdown characters so the spoken output
    sounds cleaner.
    """

    if not text:
        return ""

    cleaned = str(text)

    cleaned = re.sub(
        r"[*_`#>-]",
        "",
        cleaned,
    )

    cleaned = cleaned.replace(
        "\r",
        " ",
    )

    cleaned = cleaned.replace(
        "\n",
        " ",
    )

    cleaned = re.sub(
        r"\s+",
        " ",
        cleaned,
    )

    return cleaned.strip()


def get_transcription(audio_bytes: bytes) -> str:
    """
    Send uploaded WAV/audio bytes to Groq Whisper.
    """

    if not audio_bytes:
        raise ValueError("Audio data is empty.")

    audio_file = io.BytesIO(audio_bytes)

    audio_file.name = "megatron.wav"

    transcription = groq_client.audio.transcriptions.create(
        file=audio_file,
        model=STT_MODEL,
        response_format="json",
    )

    text = getattr(
        transcription,
        "text",
        "",
    )

    text = str(text).strip()

    if not text:
        raise ValueError(
            "Speech could not be transcribed."
        )

    return text


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
            f"[CHAT] {message}"
        )

        print(
            f"[REPLY] {reply}"
        )

        print(
            f"[TIMING] api_chat={elapsed:.3f}s"
        )

        return {
            "success": True,
            "reply": str(reply),
            "latency_seconds": round(
                elapsed,
                3,
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
# VOICE
# ============================================================

@app.post("/voice")
async def voice(
    audio: bytes = Body(
        ...,
        media_type="audio/wav",
    )
):

    started = time.perf_counter()

    try:

        print(
            f"[VOICE] Received audio: {len(audio)} bytes"
        )

        # ----------------------------------------------------
        # 1. SPEECH -> TEXT
        # ----------------------------------------------------

        transcript = get_transcription(
            audio
        )

        print(
            f"[STT] {transcript}"
        )

        # ----------------------------------------------------
        # 2. TEXT -> MEGATRON BRAIN
        # ----------------------------------------------------

        reply = process_message(
            transcript
        )

        reply = str(reply).strip()

        print(
            f"[AI] {reply}"
        )

        # ----------------------------------------------------
        # 3. CREATE TTS URL
        # ----------------------------------------------------

        clean_reply = clean_tts_text(
            reply
        )

        tts_url = (
            "/tts?text="
            + quote(
                clean_reply,
                safe="",
            )
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
            "reply": reply,
            "tts_url": tts_url,
            "latency_seconds": round(
                elapsed,
                3,
            ),
        }

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
# TTS
# ============================================================

@app.get("/tts")
async def tts(
    text: str,
):

    text = clean_tts_text(
        text
    )

    if not text:
        return {
            "success": False,
            "error": "TTS text cannot be empty.",
        }

    # Keep accidental giant requests under control.
    text = text[:1200]

    output_path = (
        f"/tmp/megatron_tts_{time.time_ns()}.mp3"
    )

    try:

        communicate = edge_tts.Communicate(
            text,
            TTS_VOICE,
        )

        await communicate.save(
            output_path
        )

        print(
            f"[TTS] Generated speech for: {text}"
        )

        return FileResponse(
            output_path,
            media_type="audio/mpeg",
            filename="megatron.mp3",
        )

    except Exception as error:

        print(
            "[TTS ERROR]",
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
        port=int(
            os.getenv(
                "PORT",
                "8000",
            )
        ),
    )

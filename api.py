import json
import os
import re
import time
from urllib.parse import quote

import edge_tts
import uvicorn
from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from google import genai
from google.genai import types


# ============================================================
# ENVIRONMENT
# ============================================================

GEMINI_API_KEY = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
)

# KEEP YOUR EXISTING GEMINI_MODEL ENVIRONMENT VARIABLE.
# Example: gemini-3.5-flash
GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash",
)

TTS_VOICE = os.getenv(
    "MEGATRON_TTS_VOICE",
    "en-US-AriaNeural",
)


if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY or GOOGLE_API_KEY not found."
    )


gemini_client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Megatron Gemini API",
    version="4.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# MEGATRON PROMPT
# ============================================================

SYSTEM_PROMPT = """
You are Megatron, a natural AI voice assistant.

Personality:
- Calm, intelligent, friendly, confident.
- Natural Indian English/Hinglish.
- Match the user's language.
- Do not sound robotic.
- Do not use unnecessary greetings.
- Do not repeat the user's question.
- Keep simple answers short.
- Be accurate.
- Never invent facts.

Voice behavior:
- The user may speak Hindi, English, or Hinglish.
- Understand normal speech-recognition mistakes.
- Clean obvious mistakes when the intended wording is clear.
- Example:
  "capital gaya hai" -> "capital kya hai"
  "news farch" -> "news search"
  "system infomation" -> "system information"

Activation greeting:
- When appropriate, Megatron's activation greeting is exactly:
  "Hi Mate!"
""".strip()


# ============================================================
# TEXT CLEANING
# ============================================================

def clean_text(text: str) -> str:
    text = str(text or "")
    text = text.replace("\r", " ")
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_tts_text(text: str) -> str:
    text = str(text or "")

    text = re.sub(
        r"[*_`#>-]",
        "",
        text,
    )

    text = text.replace(
        "\r",
        " ",
    )

    text = text.replace(
        "\n",
        " ",
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


# ============================================================
# GEMINI TEXT
# ============================================================

def gemini_text(
    prompt: str,
    retries: int = 2,
) -> str:

    last_error = None

    for attempt in range(retries + 1):

        try:

            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )

            result = clean_text(
                getattr(
                    response,
                    "text",
                    "",
                )
            )

            if not result:
                raise RuntimeError(
                    "Gemini returned an empty response."
                )

            return result

        except Exception as error:

            last_error = error

            error_text = str(error).upper()

            temporary = (
                "503" in error_text
                or "UNAVAILABLE" in error_text
                or "TIMEOUT" in error_text
            )

            if not temporary or attempt >= retries:
                raise

            wait_seconds = 2 * (
                attempt + 1
            )

            print(
                f"[GEMINI RETRY] waiting "
                f"{wait_seconds}s..."
            )

            time.sleep(
                wait_seconds
            )

    raise last_error


# ============================================================
# GEMINI VOICE
#
# ONE REQUEST:
# AUDIO -> TRANSCRIPT + AI REPLY
# ============================================================

def gemini_voice(
    audio_bytes: bytes,
    retries: int = 2,
):

    if not audio_bytes:
        raise ValueError(
            "Audio data is empty."
        )

    prompt = f"""
{SYSTEM_PROMPT}

You are receiving an audio recording from the user.

Do BOTH tasks in one response:

1. Transcribe exactly what the user intended to say.
2. Answer that request as Megatron.

Return ONLY valid JSON in exactly this structure:

{{
  "transcript": "cleaned transcription here",
  "reply": "Megatron response here"
}}

Rules:
- transcript must contain only the cleaned user's speech.
- reply must contain only Megatron's answer.
- Do not add markdown.
- Do not add explanations outside the JSON.
""".strip()

    last_error = None

    for attempt in range(retries + 1):

        try:

            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    types.Part.from_text(
                        text=prompt
                    ),
                    types.Part.from_bytes(
                        data=audio_bytes,
                        mime_type="audio/wav",
                    ),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )

            raw = str(
                getattr(
                    response,
                    "text",
                    "",
                )
            ).strip()

            if not raw:
                raise RuntimeError(
                    "Gemini returned an empty voice response."
                )

            # Remove accidental markdown code fences.
            raw = re.sub(
                r"^```(?:json)?\s*",
                "",
                raw,
                flags=re.IGNORECASE,
            )

            raw = re.sub(
                r"\s*```$",
                "",
                raw,
            )

            data = json.loads(
                raw
            )

            transcript = clean_text(
                data.get(
                    "transcript",
                    "",
                )
            )

            reply = clean_text(
                data.get(
                    "reply",
                    "",
                )
            )

            if not transcript:
                raise RuntimeError(
                    "Gemini returned no transcript."
                )

            if not reply:
                raise RuntimeError(
                    "Gemini returned no reply."
                )

            return (
                transcript,
                reply,
            )

        except Exception as error:

            last_error = error

            error_text = str(error).upper()

            temporary = (
                "503" in error_text
                or "UNAVAILABLE" in error_text
                or "TIMEOUT" in error_text
            )

            if not temporary or attempt >= retries:
                raise

            wait_seconds = 2 * (
                attempt + 1
            )

            print(
                f"[VOICE RETRY] waiting "
                f"{wait_seconds}s..."
            )

            time.sleep(
                wait_seconds
            )

    raise last_error


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {
        "service": "Megatron Gemini API",
        "status": "online",
        "version": "4.0.0",
    }


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "service": "megatron-gemini",
        "model": GEMINI_MODEL,
    }


# ============================================================
# CHAT
# ============================================================

@app.post("/chat")
def chat(
    payload: dict = Body(...)
):

    started = time.perf_counter()

    message = ""

    if isinstance(
        payload,
        dict,
    ):
        message = clean_text(
            payload.get(
                "message",
                "",
            )
        )

    if not message:

        return {
            "success": False,
            "error": "Message cannot be empty.",
        }

    try:

        prompt = f"""
{SYSTEM_PROMPT}

The user said:

{message}

Answer the user's request naturally.
""".strip()

        reply = gemini_text(
            prompt
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
            f"[TIMING] "
            f"chat={elapsed:.3f}s"
        )

        return {
            "success": True,
            "reply": reply,
            "latency_seconds": round(
                elapsed,
                3,
            ),
        }

    except Exception as error:

        print(
            "[CHAT ERROR]",
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
def voice(
    audio: bytes = Body(...)
):

    started = time.perf_counter()

    try:

        if not audio:

            return {
                "success": False,
                "error": "Audio data is empty.",
            }

        print(
            f"[VOICE] Received "
            f"{len(audio)} bytes"
        )

        # ONE Gemini request:
        # audio -> transcript + reply
        transcript, reply = gemini_voice(
            audio
        )

        print(
            f"[STT] {transcript}"
        )

        print(
            f"[AI] {reply}"
        )

        # TTS URL for ESP32.
        tts_text = clean_tts_text(
            reply
        )

        tts_url = (
            "/tts?text="
            + quote(
                tts_text,
                safe="",
            )
        )

        elapsed = (
            time.perf_counter()
            - started
        )

        print(
            f"[TIMING] "
            f"voice={elapsed:.3f}s"
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

    # Prevent excessively large requests.
    text = text[:1200]

    try:

        communicator = edge_tts.Communicate(
            text,
            TTS_VOICE,
        )

        audio_chunks = []

        async for chunk in communicator.stream():

            if chunk["type"] == "audio":

                audio_chunks.append(
                    chunk["data"]
                )

        audio_data = b"".join(
            audio_chunks
        )

        if not audio_data:

            raise RuntimeError(
                "TTS returned empty audio."
            )

        return Response(
            content=audio_data,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition":
                    'inline; filename="megatron.mp3"'
            },
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
# START
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

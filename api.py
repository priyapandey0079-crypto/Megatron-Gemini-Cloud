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

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY or GOOGLE_API_KEY not found in environment variables."
    )

# Normal text model. Keep your existing Render variable untouched.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

# Voice-capable fallback chain. The first model is the normal model;
# the others are automatic fallbacks if Gemini returns a temporary
# unavailable/overloaded response.
VOICE_MODELS = [
    model.strip()
    for model in os.getenv(
        "GEMINI_VOICE_MODELS",
        "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-2.5-flash,gemini-2.5-flash-lite",
    ).split(",")
    if model.strip()
]

# Make sure the configured normal model is tried first.
if GEMINI_MODEL not in VOICE_MODELS:
    VOICE_MODELS.insert(0, GEMINI_MODEL)

TTS_VOICE = os.getenv(
    "MEGATRON_TTS_VOICE",
    "en-US-AriaNeural",
)


gemini_client = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Megatron Gemini API",
    version="5.0.0",
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
- Speak naturally in Indian English/Hinglish.
- Match the user's language.
- Never sound robotic or overly formal.
- Do not add unnecessary greetings.
- Do not repeat the user's question.
- Keep simple answers concise.
- Do not invent facts.
- Be honest when uncertain.

Voice:
- The user may speak Hindi, English, or Hinglish.
- Understand natural speech and obvious transcription mistakes.
- Clean obvious mistakes when the intended wording is clear.
- Example: "capital gaya hai" -> "capital kya hai".
- Example: "news farch" -> "news search".
- Example: "system infomation" -> "system information".
""".strip()


# ============================================================
# HELPERS
# ============================================================

def clean_text(text: str) -> str:
    text = str(text or "")
    text = text.replace("\r", " ")
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_tts_text(text: str) -> str:
    text = str(text or "")
    text = re.sub(r"[*_`#>-]", "", text)
    text = text.replace("\r", " ")
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_temporary_gemini_error(error: Exception) -> bool:
    message = str(error).upper()
    return any(
        marker in message
        for marker in (
            "503",
            "UNAVAILABLE",
            "429",
            "RESOURCE_EXHAUSTED",
            "TIMEOUT",
            "DEADLINE",
        )
    )


def generate_text(prompt: str) -> tuple[str, str]:
    last_error = None

    for model in [GEMINI_MODEL]:
        try:
            response = gemini_client.models.generate_content(
                model=model,
                contents=prompt,
            )
            result = clean_text(getattr(response, "text", ""))
            if not result:
                raise RuntimeError("Gemini returned an empty response.")
            return result, model
        except Exception as error:
            last_error = error
            if not is_temporary_gemini_error(error):
                raise

    raise last_error


def generate_voice_response(audio_bytes: bytes) -> tuple[str, str, str]:
    if not audio_bytes:
        raise ValueError("Audio data is empty.")

    prompt = f"""
{SYSTEM_PROMPT}

You are receiving a short voice recording from the user.

Do two things from the same audio:
1. Understand and cleanly transcribe what the user intended to say.
2. Answer the user's request as Megatron.

Return ONLY valid JSON with these exact keys:

{{
  "transcript": "cleaned user speech",
  "reply": "Megatron's answer"
}}

Do not output markdown or any extra text outside the JSON.
""".strip()

    last_error = None

    for model in VOICE_MODELS:
        for attempt in range(2):
            try:
                response = gemini_client.models.generate_content(
                    model=model,
                    contents=[
                        types.Part.from_text(text=prompt),
                        types.Part.from_bytes(
                            data=audio_bytes,
                            mime_type="audio/wav",
                        ),
                    ],
                    config=types.GenerateContentConfig(
                        temperature=0.2,
                        response_mime_type="application/json",
                    ),
                )

                raw = clean_text(getattr(response, "text", ""))

                if not raw:
                    raise RuntimeError(
                        f"Gemini returned an empty voice response using {model}."
                    )

                raw = re.sub(
                    r"^```json\s*",
                    "",
                    raw,
                    flags=re.IGNORECASE,
                )
                raw = re.sub(
                    r"^```\s*",
                    "",
                    raw,
                )
                raw = re.sub(
                    r"\s*```$",
                    "",
                    raw,
                )

                data = json.loads(raw)

                transcript = clean_text(
                    data.get("transcript", "")
                )
                reply = clean_text(
                    data.get("reply", "")
                )

                if not transcript:
                    raise RuntimeError(
                        "Gemini returned no transcript."
                    )
                if not reply:
                    raise RuntimeError(
                        "Gemini returned no reply."
                    )

                return transcript, reply, model

            except Exception as error:
                last_error = error

                if not is_temporary_gemini_error(error):
                    raise

                if attempt == 0:
                    time.sleep(1.5)

        print(
            f"[VOICE] Model unavailable, trying next model: {model}"
        )

    raise last_error


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "service": "Megatron Gemini API",
        "status": "online",
        "version": "5.0.0",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "megatron-gemini",
        "model": GEMINI_MODEL,
        "voice_models": VOICE_MODELS,
    }


@app.post("/chat")
def chat(payload: dict = Body(...)):
    started = time.perf_counter()

    message = ""
    if isinstance(payload, dict):
        message = clean_text(payload.get("message", ""))

    if not message:
        return {
            "success": False,
            "error": "Message cannot be empty.",
        }

    prompt = f"""
{SYSTEM_PROMPT}

The user said:
{message}

Answer naturally.
""".strip()

    try:
        reply, model = generate_text(prompt)
        elapsed = time.perf_counter() - started

        print(f"[CHAT] {message}")
        print(f"[REPLY] {reply}")
        print(f"[MODEL] {model}")

        return {
            "success": True,
            "reply": reply,
            "model": model,
            "latency_seconds": round(elapsed, 3),
        }

    except Exception as error:
        print("[CHAT ERROR]", error)
        return {
            "success": False,
            "error": str(error),
        }


@app.post("/voice")
def voice(audio: bytes = Body(...)):
    started = time.perf_counter()

    try:
        if not audio:
            return {
                "success": False,
                "error": "Audio data is empty.",
            }

        print(
            f"[VOICE] Received {len(audio)} bytes"
        )

        transcript, reply, model = generate_voice_response(
            audio
        )

        tts_text = clean_tts_text(reply)
        tts_url = "/tts?text=" + quote(
            tts_text,
            safe="",
        )

        elapsed = time.perf_counter() - started

        print(f"[STT] {transcript}")
        print(f"[AI] {reply}")
        print(f"[VOICE MODEL] {model}")
        print(f"[TIMING] voice={elapsed:.3f}s")

        return {
            "success": True,
            "transcript": transcript,
            "reply": reply,
            "tts_url": tts_url,
            "voice_model": model,
            "latency_seconds": round(elapsed, 3),
        }

    except Exception as error:
        print("[VOICE ERROR]", error)
        return {
            "success": False,
            "error": str(error),
        }


@app.get("/tts")
async def tts(text: str):
    text = clean_tts_text(text)[:1200]

    if not text:
        return {
            "success": False,
            "error": "TTS text cannot be empty.",
        }

    try:
        communicator = edge_tts.Communicate(
            text,
            TTS_VOICE,
        )

        audio_chunks = []

        async for chunk in communicator.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])

        audio_data = b"".join(audio_chunks)

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
        print("[TTS ERROR]", error)
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
            os.getenv("PORT", "8000")
        ),
    )

import os
import time
from urllib.parse import quote

import edge_tts
import uvicorn
from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from google import genai
from google.genai import types

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
TTS_VOICE = os.getenv("MEGATRON_TTS_VOICE", "en-US-AriaNeural")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY not found in environment variables.")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)

app = FastAPI(title="Megatron Gemini API", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SYSTEM_PROMPT = """
You are Megatron, a natural AI voice assistant.

Personality:
- Calm, intelligent, friendly, confident.
- Speak naturally in Indian English/Hinglish.
- Match the user's language.
- Avoid robotic or overly formal wording.
- For simple questions, answer directly in 1-3 short sentences.
- Do not add unnecessary greetings.
- Do not repeat the user's question.
- Do not invent facts.
- Be concise for voice responses.
""".strip()


def clean_text(text: str) -> str:
    text = str(text or "").strip().replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())


def gemini_text(prompt: str) -> str:
    response = gemini_client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    result = clean_text(getattr(response, "text", ""))
    if not result:
        raise RuntimeError("Gemini returned an empty response.")
    return result


def gemini_transcribe(audio_bytes: bytes) -> str:
    if not audio_bytes:
        raise ValueError("Audio data is empty.")
    prompt = f"""
{SYSTEM_PROMPT}

Transcribe the following user's speech accurately.
The user may speak Hindi, English, or Hinglish.
Clean obvious speech-recognition mistakes when the intended wording is clear.
Examples: 'capital gaya hai' -> 'capital kya hai'; 'news farch' -> 'news search'; 'system infomation' -> 'system information'.
Return ONLY the cleaned transcription.
""".strip()
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_text(text=prompt),
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
        ],
    )
    transcript = clean_text(getattr(response, "text", ""))
    if not transcript:
        raise RuntimeError("Gemini could not transcribe the audio.")
    return transcript


@app.get("/")
def root():
    return {"service": "Megatron Gemini API", "status": "online", "version": "3.0.0"}


@app.get("/health")
def health():
    return {"status": "ok", "service": "megatron-gemini"}


@app.post("/chat")
def chat(payload: dict = Body(...)):
    started = time.perf_counter()
    message = clean_text(payload.get("message", "") if isinstance(payload, dict) else "")
    if not message:
        return {"success": False, "error": "Message cannot be empty."}
    try:
        reply = gemini_text(f"{SYSTEM_PROMPT}\n\nThe user said:\n\n{message}\n\nAnswer the user's request naturally.")
        elapsed = time.perf_counter() - started
        print(f"[CHAT] {message}")
        print(f"[REPLY] {reply}")
        print(f"[TIMING] {elapsed:.3f}s")
        return {"success": True, "reply": reply, "latency_seconds": round(elapsed, 3)}
    except Exception as error:
        print("[CHAT ERROR]", error)
        return {"success": False, "error": str(error)}


@app.post("/voice")
async def voice(audio: bytes = Body(...)):
    started = time.perf_counter()
    try:
        if not audio:
            return {"success": False, "error": "Audio data is empty."}
        print(f"[VOICE] Received {len(audio)} bytes")
        transcript = gemini_transcribe(audio)
        print(f"[STT] {transcript}")
        reply = gemini_text(f"You are Megatron.\n\n{SYSTEM_PROMPT}\n\nThe user's cleaned speech transcription is:\n\n{transcript}\n\nAnswer the user's request naturally.")
        print(f"[AI] {reply}")
        tts_url = "/tts?text=" + quote(reply, safe="")
        elapsed = time.perf_counter() - started
        print(f"[TIMING] voice={elapsed:.3f}s")
        return {"success": True, "transcript": transcript, "reply": reply, "tts_url": tts_url, "latency_seconds": round(elapsed, 3)}
    except Exception as error:
        print("[VOICE ERROR]", error)
        return {"success": False, "error": str(error)}


@app.get("/tts")
async def tts(text: str):
    text = clean_text(text)[:1200]
    if not text:
        return {"success": False, "error": "TTS text cannot be empty."}
    try:
        communicator = edge_tts.Communicate(text, TTS_VOICE)
        audio_chunks = []
        async for chunk in communicator.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])
        audio_data = b"".join(audio_chunks)
        if not audio_data:
            raise RuntimeError("TTS returned empty audio.")
        return Response(
            content=audio_data,
            media_type="audio/mpeg",
            headers={"Content-Disposition": 'inline; filename="megatron.mp3"'},
        )
    except Exception as error:
        print("[TTS ERROR]", error)
        return {"success": False, "error": str(error)}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))

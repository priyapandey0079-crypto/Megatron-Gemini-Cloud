import asyncio
import io
import json
import os
import re
import time
import wave
from urllib.parse import quote

import av
import edge_tts
import numpy as np
import uvicorn
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
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

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

VOICE_MODELS = [
    model.strip()
    for model in os.getenv(
        "GEMINI_VOICE_MODELS",
        "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-2.5-flash,gemini-2.5-flash-lite",
    ).split(",")
    if model.strip()
]

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
    version="5.1.0",
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

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )

        result = clean_text(
            getattr(response, "text", "")
        )

        if not result:
            raise RuntimeError(
                "Gemini returned an empty response."
            )

        return result, GEMINI_MODEL

    except Exception as error:
        last_error = error

        if not is_temporary_gemini_error(error):
            raise

    raise last_error


def generate_voice_response(audio_bytes: bytes) -> tuple[str, str, str]:
    if not audio_bytes:
        raise ValueError(
            "Audio data is empty."
        )

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

Do not output markdown.
Do not output any extra text outside the JSON.
""".strip()

    last_error = None

    for model in VOICE_MODELS:
        for attempt in range(2):
            try:
                response = gemini_client.models.generate_content(
                    model=model,
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
                        temperature=0.2,
                        response_mime_type="application/json",
                    ),
                )

                raw = clean_text(
                    getattr(response, "text", "")
                )

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
# OPUS DECODER
# ============================================================

def decode_opus_to_wav(
    packets: list[bytes],
) -> bytes:

    if not packets:
        return b""

    decoder = av.CodecContext.create(
        "opus",
        "r",
    )

    decoder.sample_rate = 16000
    decoder.layout = "mono"
    decoder.open()

    resampler = av.audio.resampler.AudioResampler(
        format="s16",
        layout="mono",
        rate=16000,
    )

    pcm_parts = []

    for opus_packet in packets:
        if not opus_packet:
            continue

        packet = av.Packet(
            opus_packet
        )

        try:
            frames = decoder.decode(
                packet
            )
        except Exception as error:
            print(
                "[OPUS DECODE ERROR]",
                repr(error),
            )
            continue

        for frame in frames:
            try:
                converted = resampler.resample(
                    frame
                )
            except Exception as error:
                print(
                    "[RESAMPLE ERROR]",
                    repr(error),
                )
                continue

            if not isinstance(
                converted,
                list,
            ):
                converted = [converted]

            for out_frame in converted:
                array = out_frame.to_ndarray()

                if array.ndim > 1:
                    array = array[0]

                pcm_parts.append(
                    array.astype(
                        np.int16,
                        copy=False,
                    ).tobytes()
                )

    try:
        remaining = decoder.decode(
            None
        )

        for frame in remaining:
            converted = resampler.resample(
                frame
            )

            if not isinstance(
                converted,
                list,
            ):
                converted = [converted]

            for out_frame in converted:
                array = out_frame.to_ndarray()

                if array.ndim > 1:
                    array = array[0]

                pcm_parts.append(
                    array.astype(
                        np.int16,
                        copy=False,
                    ).tobytes()
                )

    except Exception:
        pass

    pcm = b"".join(
        pcm_parts
    )

    if not pcm:
        return b""

    wav_buffer = io.BytesIO()

    with wave.open(
        wav_buffer,
        "wb",
    ) as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)

    return wav_buffer.getvalue()


# ============================================================
# EDGE TTS -> OPUS
# ============================================================

async def generate_tts_opus(
    text: str,
    sample_rate: int = 24000,
) -> list[bytes]:

    text = clean_tts_text(
        text
    )

    if not text:
        raise RuntimeError(
            "TTS text is empty."
        )

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

    mp3_data = b"".join(
        audio_chunks
    )

    if not mp3_data:
        raise RuntimeError(
            "Edge TTS returned empty audio."
        )

    def encode_opus() -> list[bytes]:
        input_buffer = io.BytesIO(
            mp3_data
        )

        pcm_parts = []

        with av.open(
            input_buffer,
            format="mp3",
        ) as container:

            audio_stream = container.streams.audio[0]

            resampler = av.audio.resampler.AudioResampler(
                format="s16",
                layout="mono",
                rate=sample_rate,
            )

            for frame in container.decode(
                audio_stream
            ):
                converted = resampler.resample(
                    frame
                )

                if not isinstance(
                    converted,
                    list,
                ):
                    converted = [converted]

                for out_frame in converted:
                    array = out_frame.to_ndarray()

                    if array.ndim > 1:
                        array = array[0]

                    pcm_parts.append(
                        array.astype(
                            np.int16,
                            copy=False,
                        )
                    )

        if not pcm_parts:
            raise RuntimeError(
                "TTS decoding produced no PCM."
            )

        pcm = np.concatenate(
            pcm_parts
        ).astype(
            np.int16,
            copy=False,
        )

        encoder = av.CodecContext.create(
            "opus",
            "w",
        )

        encoder.sample_rate = sample_rate
        encoder.layout = "mono"
        encoder.open()

        packets = []

        samples_per_packet = (
            sample_rate * 60 // 1000
        )

        offset = 0

        while offset < len(pcm):
            remaining = len(pcm) - offset

            if remaining >= samples_per_packet:
                chunk = pcm[
                    offset:
                    offset + samples_per_packet
                ]

                offset += samples_per_packet

            else:
                chunk = np.zeros(
                    samples_per_packet,
                    dtype=np.int16,
                )

                chunk[:remaining] = pcm[
                    offset:
                ]

                offset = len(pcm)

            audio_frame = av.AudioFrame.from_ndarray(
                chunk.reshape(
                    1,
                    -1,
                ),
                format="s16",
                layout="mono",
            )

            audio_frame.sample_rate = sample_rate

            encoded_packets = encoder.encode(
                audio_frame
            )

            packets.extend(
                bytes(packet)
                for packet in encoded_packets
            )

        final_packets = encoder.encode(
            None
        )

        packets.extend(
            bytes(packet)
            for packet in final_packets
        )

        return packets

    return await asyncio.to_thread(
        encode_opus
    )


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "service": "Megatron Gemini API",
        "status": "online",
        "version": "5.1.0",
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
def chat(
    payload: dict = Body(...),
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

    prompt = f"""
{SYSTEM_PROMPT}

The user said:
{message}

Answer naturally.
""".strip()

    try:
        reply, model = generate_text(
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
            f"[MODEL] {model}"
        )

        return {
            "success": True,
            "reply": reply,
            "model": model,
            "latency_seconds": round(
                elapsed,
                3,
            ),
        }

    except Exception as error:
        print(
            "[CHAT ERROR]",
            repr(error),
        )

        return {
            "success": False,
            "error": str(error),
        }


@app.post("/voice")
def voice(
    audio: bytes = Body(...),
):
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

        transcript, reply, model = (
            generate_voice_response(
                audio
            )
        )

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
            f"[STT] {transcript}"
        )

        print(
            f"[AI] {reply}"
        )

        print(
            f"[VOICE MODEL] {model}"
        )

        print(
            f"[TIMING] voice={elapsed:.3f}s"
        )

        return {
            "success": True,
            "transcript": transcript,
            "reply": reply,
            "tts_url": tts_url,
            "voice_model": model,
            "latency_seconds": round(
                elapsed,
                3,
            ),
        }

    except Exception as error:
        print(
            "[VOICE ERROR]",
            repr(error),
        )

        return {
            "success": False,
            "error": str(error),
        }


@app.get("/tts")
async def tts(
    text: str,
):
    text = clean_tts_text(
        text
    )[:1200]

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
            repr(error),
        )

        return {
            "success": False,
            "error": str(error),
        }


# ============================================================
# XIAOZHI WEBSOCKET
# ============================================================

@app.websocket("/xiaozhi")
async def xiaozhi_websocket(
    websocket: WebSocket,
):
    await websocket.accept()

    print(
        "[XIAOZHI WS] Connected"
    )

    audio_packets: list[bytes] = []
    session_id = None
    listening = False

    async def send_json(
        data: dict,
    ):
        await websocket.send_text(
            json.dumps(data)
        )

    try:
        while True:
            message = await websocket.receive()

            # ==================================================
            # TEXT / JSON
            # ==================================================

            if message.get("text") is not None:
                raw = message["text"]

                print(
                    "[XIAOZHI WS] TEXT:",
                    raw,
                )

                try:
                    data = json.loads(
                        raw
                    )
                except json.JSONDecodeError:
                    print(
                        "[XIAOZHI WS] Invalid JSON"
                    )
                    continue

                msg_type = data.get(
                    "type"
                )

                # ------------------------------------------------
                # HELLO
                # ------------------------------------------------

                if msg_type == "hello":
                    session_id = (
                        f"megatron-{int(time.time() * 1000)}"
                    )

                    server_hello = {
                        "type": "hello",
                        "transport": "websocket",
                        "session_id": session_id,
                        "audio_params": {
                            "format": "opus",
                            "sample_rate": 24000,
                            "channels": 1,
                            "frame_duration": 60,
                        },
                    }

                    await send_json(
                        server_hello
                    )

                    print(
                        "[XIAOZHI WS] Sent server hello:",
                        json.dumps(
                            server_hello
                        ),
                    )

                # ------------------------------------------------
                # LISTEN
                # ------------------------------------------------

                elif msg_type == "listen":
                    state = data.get(
                        "state"
                    )

                    print(
                        "[XIAOZHI WS] LISTEN STATE:",
                        state,
                    )

                    if state == "start":
                        audio_packets.clear()
                        listening = True

                        print(
                            "[XIAOZHI WS] Recording started"
                        )

                    elif state == "stop":
                        listening = False

                        print(
                            "[XIAOZHI WS] Recording stopped:",
                            len(audio_packets),
                            "packets",
                        )

                        packets = list(
                            audio_packets
                        )

                        audio_packets.clear()

                        if not packets:
                            print(
                                "[XIAOZHI WS] No audio packets"
                            )
                            continue

                        try:
                            # ==========================================
                            # OPUS -> WAV
                            # ==========================================

                            wav_bytes = await asyncio.to_thread(
                                decode_opus_to_wav,
                                packets,
                            )

                            print(
                                "[XIAOZHI WS] Decoded WAV:",
                                len(wav_bytes),
                                "bytes",
                            )

                            if not wav_bytes:
                                raise RuntimeError(
                                    "Opus decoding produced no WAV."
                                )

                            # ==========================================
                            # GEMINI
                            # ==========================================

                            transcript, reply, model = (
                                await asyncio.to_thread(
                                    generate_voice_response,
                                    wav_bytes,
                                )
                            )

                            print(
                                "[STT]",
                                transcript,
                            )

                            print(
                                "[AI]",
                                reply,
                            )

                            print(
                                "[MODEL]",
                                model,
                            )

                            # ==========================================
                            # SEND TRANSCRIPT
                            # ==========================================

                            await send_json(
                                {
                                    "type": "stt",
                                    "text": transcript,
                                }
                            )

                            # ==========================================
                            # TTS START
                            # ==========================================

                            await send_json(
                                {
                                    "type": "tts",
                                    "state": "start",
                                }
                            )

                            await send_json(
                                {
                                    "type": "tts",
                                    "state": "sentence_start",
                                    "text": reply,
                                }
                            )

                            # ==========================================
                            # EDGE TTS -> OPUS
                            # ==========================================

                            opus_packets = (
                                await generate_tts_opus(
                                    reply,
                                    sample_rate=24000,
                                )
                            )

                            print(
                                "[XIAOZHI WS] Sending",
                                len(opus_packets),
                                "Opus response packets",
                            )

                            # ==========================================
                            # STREAM AUDIO TO ESP32
                            # ==========================================

                            for packet in opus_packets:
                                if packet:
                                    await websocket.send_bytes(
                                        packet
                                    )

                                    await asyncio.sleep(
                                        0.001
                                    )

                            # ==========================================
                            # TTS STOP
                            # ==========================================

                            await send_json(
                                {
                                    "type": "tts",
                                    "state": "stop",
                                }
                            )

                            print(
                                "[XIAOZHI WS] Response complete"
                            )

                        except Exception as error:
                            print(
                                "[XIAOZHI WS] VOICE ERROR:",
                                repr(error),
                            )

                            try:
                                await send_json(
                                    {
                                        "type": "alert",
                                        "status": "error",
                                        "message": str(error),
                                        "emotion": "warning",
                                    }
                                )
                            except Exception:
                                pass

                # ------------------------------------------------
                # ABORT
                # ------------------------------------------------

                elif msg_type == "abort":
                    print(
                        "[XIAOZHI WS] Abort received"
                    )

                    audio_packets.clear()
                    listening = False

                # ------------------------------------------------
                # OTHER JSON
                # ------------------------------------------------

                else:
                    print(
                        "[XIAOZHI WS] JSON type:",
                        msg_type,
                    )

            # ==================================================
            # BINARY AUDIO
            # ==================================================

            elif message.get("bytes") is not None:
                audio = message[
                    "bytes"
                ]

                if audio and listening:
                    audio_packets.append(
                        audio
                    )

                    packet_count = len(
                        audio_packets
                    )

                    if packet_count % 10 == 0:
                        print(
                            "[XIAOZHI WS] AUDIO packets:",
                            packet_count,
                        )

    except WebSocketDisconnect:
        print(
            "[XIAOZHI WS] Client disconnected"
        )

    except Exception as error:
        print(
            "[XIAOZHI WS] Error:",
            repr(error),
        )

        try:
            await websocket.close(
                code=1011
            )
        except Exception:
            pass


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

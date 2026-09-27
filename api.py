import asyncio
import json
import os
import random
import shutil
import struct
import subprocess
import time
import uuid
from urllib.parse import quote

import edge_tts
import uvicorn
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from google import genai
from google.genai import types

from app import process_message


# ============================================================
# ENVIRONMENT
# ============================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_VOICE_MODEL = os.getenv("GEMINI_VOICE_MODEL", "gemini-3.5-flash-lite")
TTS_VOICE = os.getenv("MEGATRON_TTS_VOICE", "en-US-AriaNeural")

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY or GOOGLE_API_KEY not found in environment variables."
    )

gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="Megatron AI API",
    version="6.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# HELPERS
# ============================================================

def clean_text(text: str) -> str:
    text = str(text or "").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split()).strip()


def clean_tts_text(text: str) -> str:
    text = str(text or "")
    for token in ("*", "_", "`", "#", ">"):
        text = text.replace(token, "")
    return clean_text(text)


def transcribe_wav(audio_bytes: bytes) -> str:
    if not audio_bytes:
        raise ValueError("Audio data is empty.")

    prompt = """
You are transcribing speech for the Megatron voice assistant.
The speaker may use English, Hindi, or Hinglish.
Clean obvious speech-recognition mistakes when the intended wording is clear.
Examples:
- capital gaya hai -> capital kya hai
- news farch -> news search
- system infomation -> system information
Return ONLY the cleaned transcription.
""".strip()

    response = gemini_client.models.generate_content(
        model=GEMINI_VOICE_MODEL,
        contents=[
            types.Part.from_text(text=prompt),
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
        ],
    )

    transcript = clean_text(getattr(response, "text", ""))
    if not transcript:
        raise RuntimeError("Gemini could not transcribe the audio.")
    return transcript


async def tts_mp3(text: str) -> bytes:
    text = clean_tts_text(text)[:1200]
    if not text:
        raise ValueError("TTS text cannot be empty.")

    communicator = edge_tts.Communicate(text, TTS_VOICE)
    chunks = []
    async for chunk in communicator.stream():
        if chunk.get("type") == "audio":
            chunks.append(chunk["data"])

    audio = b"".join(chunks)
    if not audio:
        raise RuntimeError("Edge TTS returned empty audio.")
    return audio


# ============================================================
# OGG / OPUS BRIDGE
# ============================================================

_OGG_CRC_TABLE = None


def ogg_crc(data: bytes) -> int:
    global _OGG_CRC_TABLE
    if _OGG_CRC_TABLE is None:
        table = []
        for i in range(256):
            r = i << 24
            for _ in range(8):
                r = ((r << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if (r & 0x80000000) else (r << 1) & 0xFFFFFFFF
            table.append(r)
        _OGG_CRC_TABLE = table

    crc = 0
    for byte in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _OGG_CRC_TABLE[((crc >> 24) & 0xFF) ^ byte]
    return crc


def ogg_page(packet: bytes, sequence: int, granule_position: int, header_type: int, serial: int) -> bytes:
    segments = [len(packet[i:i + 255]) for i in range(0, len(packet), 255)]
    if not segments:
        segments = [0]
    elif segments[-1] == 255:
        segments.append(0)

    header = (
        b"OggS"
        + bytes([0, header_type])
        + struct.pack("<QIIIB", granule_position, serial, sequence, 0, len(segments))
        + bytes(segments)
    )
    body = header + packet
    crc = ogg_crc(body)
    header = header[:22] + struct.pack("<I", crc) + header[26:]
    return header + packet


def opus_packets_to_ogg(packets: list[bytes], sample_rate: int, frame_samples: int) -> bytes:
    serial = random.getrandbits(32)
    output = bytearray()

    opus_head = (
        b"OpusHead"
        + bytes([1, 1])
        + struct.pack("<H", 0)
        + struct.pack("<I", sample_rate)
        + struct.pack("<h", 0)
        + bytes([0])
    )
    vendor = b"Megatron"
    opus_tags = b"OpusTags" + struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", 0)

    output += ogg_page(opus_head, 0, 0, 0x02, serial)
    output += ogg_page(opus_tags, 1, 0, 0x00, serial)

    granule = 0
    for index, packet in enumerate(packets):
        granule += frame_samples
        output += ogg_page(
            packet,
            index + 2,
            granule,
            0x04 if index == len(packets) - 1 else 0x00,
            serial,
        )

    return bytes(output)


def ogg_to_opus_packets(data: bytes) -> list[bytes]:
    packets = []
    current = bytearray()
    pos = 0

    while pos + 27 <= len(data):
        if data[pos:pos + 4] != b"OggS":
            raise RuntimeError("Invalid Ogg stream.")

        segment_count = data[pos + 26]
        table_start = pos + 27
        table_end = table_start + segment_count
        body_start = table_end
        if body_start > len(data):
            raise RuntimeError("Truncated Ogg segment table.")

        segments = data[table_start:table_end]
        body_len = sum(segments)
        body_end = body_start + body_len
        if body_end > len(data):
            raise RuntimeError("Truncated Ogg body.")

        body = data[body_start:body_end]
        body_pos = 0
        for size in segments:
            current += body[body_pos:body_pos + size]
            body_pos += size
            if size < 255:
                packets.append(bytes(current))
                current.clear()

        pos = body_end

    if current:
        raise RuntimeError("Incomplete final Opus packet.")
    return packets


def get_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


def opus_frames_to_wav(frames: list[bytes]) -> bytes:
    if not frames:
        raise ValueError("No Opus audio frames received.")

    # Xiaozhi v2.4.2 uses 60 ms Opus input frames at 16 kHz.
    ogg = opus_packets_to_ogg(frames, sample_rate=16000, frame_samples=960)
    ffmpeg = get_ffmpeg()

    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-i", "pipe:0",
            "-f", "wav",
            "-ar", "16000",
            "-ac", "1",
            "pipe:1",
        ],
        input=ogg,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=40,
    )
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(f"Opus decode failed: {detail}")
    return result.stdout


def mp3_to_opus_packets(mp3_data: bytes) -> list[bytes]:
    if not mp3_data:
        raise ValueError("TTS returned empty MP3 data.")

    ffmpeg = get_ffmpeg()
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-i", "pipe:0",
            "-ar", "24000",
            "-ac", "1",
            "-c:a", "libopus",
            "-application", "voip",
            "-frame_duration", "20",
            "-f", "opus",
            "pipe:1",
        ],
        input=mp3_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=40,
    )
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(f"MP3 to Opus conversion failed: {detail}")

    return [
        packet
        for packet in ogg_to_opus_packets(result.stdout)
        if not packet.startswith(b"OpusHead") and not packet.startswith(b"OpusTags")
    ]


async def send_voice_result(websocket: WebSocket, transcript: str, reply: str) -> None:
    await websocket.send_text(json.dumps({"type": "stt", "text": transcript}, ensure_ascii=False))
    await websocket.send_text(json.dumps({"type": "tts", "state": "start"}, ensure_ascii=False))
    await websocket.send_text(
        json.dumps({"type": "tts", "state": "sentence_start", "text": reply}, ensure_ascii=False)
    )

    mp3_data = await tts_mp3(reply)
    packets = await asyncio.to_thread(mp3_to_opus_packets, mp3_data)
    for packet in packets:
        await websocket.send_bytes(packet)

    await websocket.send_text(json.dumps({"type": "tts", "state": "stop"}, ensure_ascii=False))


# ============================================================
# HTTP ROUTES — KEEP EXISTING MEGATRON BACKEND
# ============================================================

@app.get("/")
def root():
    return {"service": "Megatron AI API", "status": "online", "version": "6.0.0"}


@app.get("/health")
def health():
    return {"status": "ok", "service": "megatron-ai"}


@app.post("/chat")
def chat(payload: dict = Body(...)):
    message = clean_text(payload.get("message", "") if isinstance(payload, dict) else "")
    if not message:
        return {"success": False, "error": "Message cannot be empty."}

    started = time.perf_counter()
    try:
        reply = process_message(message)
        elapsed = time.perf_counter() - started
        print(f"[CHAT] {message}")
        print(f"[REPLY] {reply}")
        print(f"[TIMING] chat={elapsed:.3f}s")
        return {
            "success": True,
            "reply": str(reply),
            "latency_seconds": round(elapsed, 3),
        }
    except Exception as error:
        print("[CHAT ERROR]", error)
        return {"success": False, "error": str(error)}


@app.post("/voice")
async def voice(audio: bytes = Body(...)):
    started = time.perf_counter()
    try:
        transcript = await asyncio.to_thread(transcribe_wav, audio)
        reply = await asyncio.to_thread(process_message, transcript)
        elapsed = time.perf_counter() - started
        return {
            "success": True,
            "transcript": transcript,
            "reply": str(reply),
            "tts_url": "/tts?text=" + quote(clean_tts_text(reply), safe=""),
            "latency_seconds": round(elapsed, 3),
        }
    except Exception as error:
        print("[VOICE ERROR]", error)
        return {"success": False, "error": str(error)}


@app.get("/tts")
async def tts(text: str):
    try:
        audio = await tts_mp3(text)
        return Response(
            content=audio,
            media_type="audio/mpeg",
            headers={"Content-Disposition": 'inline; filename="megatron.mp3"'},
        )
    except Exception as error:
        print("[TTS ERROR]", error)
        return {"success": False, "error": str(error)}


# ============================================================
# WEBSOCKET ROUTE FOR ESP32 / XIAOZHI AUDIO STACK
# ============================================================

@app.websocket("/xiaozhi")
async def xiaozhi_websocket(websocket: WebSocket):
    await websocket.accept()
    session_id = str(uuid.uuid4())
    listening = False
    processing = False
    received_frames: list[bytes] = []

    print(f"[ESP32 WS] connected session={session_id}")

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            binary = message.get("bytes")
            text = message.get("text")

            if binary is not None:
                if listening and not processing and len(received_frames) < 1200:
                    received_frames.append(binary)
                continue

            if text is None:
                continue

            try:
                root = json.loads(text)
            except json.JSONDecodeError:
                continue

            if not isinstance(root, dict):
                continue

            msg_type = root.get("type")

            if msg_type == "hello":
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "hello",
                            "transport": "websocket",
                            "session_id": session_id,
                            "audio_params": {
                                "format": "opus",
                                "sample_rate": 24000,
                                "channels": 1,
                                "frame_duration": 20,
                            },
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                continue

            if msg_type == "ping":
                await websocket.send_text(
                    json.dumps(
                        {"id": root.get("id"), "result": {}},
                        separators=(",", ":"),
                    )
                )
                continue

            if msg_type == "listen":
                state = root.get("state")

                if state == "start":
                    listening = True
                    processing = False
                    received_frames.clear()
                    print("[ESP32 WS] listening started")

                elif state == "detect":
                    print("[ESP32 WS] wake word:", root.get("text", ""))

                elif state == "stop":
                    if not listening or processing:
                        continue

                    listening = False
                    processing = True
                    frames = list(received_frames)
                    received_frames.clear()

                    try:
                        if not frames:
                            raise RuntimeError("No audio received.")

                        print(f"[ESP32 WS] processing {len(frames)} Opus frames")
                        wav = await asyncio.to_thread(opus_frames_to_wav, frames)
                        transcript = await asyncio.to_thread(transcribe_wav, wav)
                        reply = await asyncio.to_thread(process_message, transcript)

                        print(f"[ESP32 WS] STT: {transcript}")
                        print(f"[ESP32 WS] AI: {reply}")
                        await send_voice_result(websocket, transcript, str(reply))

                    except Exception as error:
                        print("[ESP32 WS] processing error:", error)
                        await send_voice_result(
                            websocket,
                            "",
                            "Sorry, I couldn't process that request.",
                        )
                    finally:
                        processing = False

                continue

            if msg_type == "abort":
                listening = False
                processing = False
                received_frames.clear()
                await websocket.send_text(
                    json.dumps({"type": "tts", "state": "stop"}, separators=(",", ":"))
                )

    except WebSocketDisconnect:
        pass
    except Exception as error:
        print("[ESP32 WS] connection error:", error)
    finally:
        print(f"[ESP32 WS] disconnected session={session_id}")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))

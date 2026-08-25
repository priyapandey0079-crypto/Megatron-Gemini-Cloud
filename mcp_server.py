import os
import sys
import json
import asyncio
import datetime
import ast
import operator
import platform
import subprocess
import ctypes
import time
import fnmatch
import math
import struct
import wave
import re
import math
import struct
import wave
try:
    import winsound
except ImportError:
    winsound = None
from pathlib import Path
from urllib.parse import quote

import httpx
import websockets
from dotenv import load_dotenv

# ============================================================
# PROJECT PATH / ENV
# ============================================================
PROJECT_DIR = Path(__file__).resolve().parent

if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

load_dotenv()

XIAOZHI_MCP_URL = os.getenv("XIAOZHI_MCP_URL")
MEGATRON_API_URL = os.getenv(
    "MEGATRON_API_URL",
    "http://127.0.0.1:8000/chat",
)


SCREENSHOT_DIR = PROJECT_DIR / "screenshots"

if not XIAOZHI_MCP_URL:
    raise SystemExit("XIAOZHI_MCP_URL missing from .env")

SOUNDBOARD_ENABLED = os.getenv("MEGATRON_SOUNDBOARD", "1").strip().lower() not in {"0", "false", "no", "off"}

# ============================================================
# VOICE EFFECTS / SOUNDBOARD
# ============================================================
SOUNDS_DIR = PROJECT_DIR / "sounds"
SOUNDS_DIR.mkdir(parents=True, exist_ok=True)

SOUND_PATTERNS = {
    "startup": [(660, 0.07), (880, 0.09), (1047, 0.12)],
    "wake": [(880, 0.09), (1175, 0.12)],
    "tool_start": [(740, 0.07)],
    "success": [(784, 0.06), (1047, 0.09)],
    "error": [(440, 0.09), (330, 0.13)],
    "alert": [(988, 0.07), (988, 0.07), (988, 0.12)],
    "stealth": [(392, 0.06), (294, 0.09)],
    "diagnostic": [(523, 0.06), (659, 0.06), (784, 0.08), (988, 0.10)],
    "quiz_correct": [(659, 0.06), (784, 0.07), (1047, 0.10)],
    "quiz_wrong": [(392, 0.07), (330, 0.11)],
    "reminder": [(659, 0.08), (880, 0.08), (659, 0.12)],
}


def _build_wav(path: Path, tones):
    """Generate a tiny mono WAV locally so playback uses the Windows audio device."""
    sample_rate = 44100
    amplitude = 0.28
    gap = 0.018
    frames = bytearray()

    for frequency, duration in tones:
        count = max(1, int(sample_rate * duration))
        fade_frames = min(int(sample_rate * 0.008), count // 4)
        for i in range(count):
            envelope = 1.0
            if fade_frames:
                if i < fade_frames:
                    envelope = i / fade_frames
                elif i >= count - fade_frames:
                    envelope = (count - i - 1) / fade_frames
            sample = int(
                32767 * amplitude * envelope *
                math.sin(2 * math.pi * frequency * i / sample_rate)
            )
            frames.extend(struct.pack('<h', sample))
        gap_count = int(sample_rate * gap)
        frames.extend(b'\x00\x00' * gap_count)

    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(frames)


def _ensure_sound_files():
    paths = {}
    for effect, tones in SOUND_PATTERNS.items():
        path = SOUNDS_DIR / f"{effect}.wav"
        try:
            if not path.exists() or path.stat().st_size < 1000:
                _build_wav(path, tones)
            paths[effect] = path
        except Exception as error:
            print(f"Sound file creation error ({effect}): {error}")
    return paths


SOUND_FILES = _ensure_sound_files()


def play_effect(effect: str):
    """Play a generated WAV through Windows' default audio device."""
    if not SOUNDBOARD_ENABLED:
        return False, "Soundboard is disabled."

    if platform.system().lower() != "windows":
        return False, "Windows sound playback is unavailable."

    path = SOUND_FILES.get(effect)

    if not path or not path.exists():
        return False, f"Sound file for '{effect}' is unavailable."

    path_text = str(path.resolve())

    # Primary path: Windows .NET SoundPlayer in the user's desktop session.
    # PlaySync keeps the short sound alive until playback finishes.
    ps_script = (
        "$p = New-Object System.Media.SoundPlayer "
        f"([System.IO.Path]::GetFullPath('{path_text.replace(chr(39), chr(39)*2)}')); "
        "$p.Load(); $p.PlaySync();"
    )

    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-Command",
                ps_script,
            ],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        if result.returncode == 0:
            return True, f"Sound effect played: {effect}."

        print(
            "[SOUNDBOARD] PowerShell SoundPlayer failed:",
            (result.stderr or result.stdout).strip()
        )

    except Exception as error:
        print("[SOUNDBOARD] PowerShell playback error:", error)

    # Fallback: winsound.
    if winsound is not None:
        try:
            winsound.PlaySound(
                path_text,
                winsound.SND_FILENAME,
            )
            return True, f"Sound effect played: {effect}."
        except Exception as error:
            print("[SOUNDBOARD] winsound fallback failed:", error)

    return False, f"Sound playback failed for '{effect}'."



def soundboard_tool(args):
    action = str(args.get("action", "list")).strip().lower()
    aliases = {
        "start": "startup",
        "beep": "startup",
        "startup sequence": "startup",
        "wake": "wake",
        "system diagnostic": "diagnostic",
        "diagnostic": "diagnostic",
        "stealth mode": "stealth",
        "stealth": "stealth",
        "alert mode": "alert",
        "alert": "alert",
        "success": "success",
        "error": "error",
        "quiz correct": "quiz_correct",
        "quiz wrong": "quiz_wrong",
        "reminder": "reminder",
    }

    if action in {"list", "help"}:
        return {
            "content": [{
                "type": "text",
                "text": (
                    "Soundboard effects: beep, startup, wake, diagnostic, stealth, "
                    "alert, success, error, quiz_correct, quiz_wrong, reminder."
                ),
            }]
        }

    effect = aliases.get(action, action)
    if effect not in SOUND_PATTERNS:
        return {
            "content": [{"type": "text", "text": f"Unknown sound effect: {action}"}],
            "isError": True,
        }

    ok, message = play_effect(effect)
    return {
        "content": [{"type": "text", "text": message}],
        "isError": not ok,
    }


# ============================================================
# OPTIONAL WEB SEARCH
# ============================================================
try:
    from tools.web_search import web_search
except Exception as error:
    web_search = None
    print("Web search module could not be loaded:", error)

# ============================================================
# SEND JSON
# ============================================================
async def send_json(ws, data):
    message = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    print("\nMEGATRON ->")
    print(message)

    await ws.send(message)

# ============================================================
# SAFE CALCULATOR
# ============================================================
def safe_calculate(expression):
    allowed_operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)

        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("Invalid number")

        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)

            operation = allowed_operators.get(type(node.op))

            if operation is None:
                raise ValueError("Operator not allowed")

            return operation(left, right)

        if isinstance(node, ast.UnaryOp):
            value = evaluate(node.operand)

            operation = allowed_operators.get(type(node.op))

            if operation is None:
                raise ValueError("Operator not allowed")

            return operation(value)

        raise ValueError("Invalid expression")

    tree = ast.parse(expression, mode="eval")

    return evaluate(tree)

# ============================================================
# WINDOWS POWERSHELL JSON
# ============================================================
def powershell_json(script):
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            ),
        )

        if result.returncode != 0:
            return None

        output = result.stdout.strip()

        if not output:
            return None

        return json.loads(output)

    except Exception:
        return None

# ============================================================
# SYSTEM INFO
# ============================================================
def get_windows_system_info():
    info = {
        "computer_name": os.environ.get(
            "COMPUTERNAME",
            "Unknown",
        ),
        "operating_system": "Windows",
        "windows_version": platform.version(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
    }

    computer = powershell_json(
        """
        $x = Get-CimInstance Win32_ComputerSystem
        [PSCustomObject]@{
            Manufacturer = $x.Manufacturer
            Model = $x.Model
            RAM_GB = [math]::Round($x.TotalPhysicalMemory / 1GB, 2)
        } | ConvertTo-Json -Compress
        """
    )

    if isinstance(computer, dict):
        info["manufacturer"] = computer.get(
            "Manufacturer",
            "Unknown",
        )
        info["model"] = computer.get(
            "Model",
            "Unknown",
        )
        info["ram_gb"] = computer.get(
            "RAM_GB",
            "Unknown",
        )

    cpu = powershell_json(
        """
        $x = Get-CimInstance Win32_Processor | Select-Object -First 1
        [PSCustomObject]@{
            Name = $x.Name
            Cores = $x.NumberOfCores
            LogicalProcessors = $x.NumberOfLogicalProcessors
            MaxClockMHz = $x.MaxClockSpeed
        } | ConvertTo-Json -Compress
        """
    )

    if isinstance(cpu, dict):
        info["cpu"] = cpu.get(
            "Name",
            "Unknown",
        )
        info["cpu_cores"] = cpu.get(
            "Cores",
            "Unknown",
        )
        info["logical_processors"] = cpu.get(
            "LogicalProcessors",
            "Unknown",
        )
        info["max_clock_mhz"] = cpu.get(
            "MaxClockMHz",
            "Unknown",
        )

    gpu = powershell_json(
        """
        $x = Get-CimInstance Win32_VideoController |
             Where-Object { $_.Name -and $_.Name -notmatch "Microsoft Basic" } |
             Select-Object Name, AdapterRAM
        @($x) | ConvertTo-Json -Compress
        """
    )

    if isinstance(gpu, dict):
        gpu = [gpu]

    if isinstance(gpu, list):
        gpu_list = []

        for item in gpu:
            if not isinstance(item, dict):
                continue

            name = item.get(
                "Name",
                "Unknown",
            )

            vram = item.get("AdapterRAM")

            vram_gb = (
                round(vram / (1024 ** 3), 2)
                if isinstance(vram, (int, float))
                else "Unknown"
            )

            gpu_list.append(
                {
                    "name": name,
                    "vram_gb": vram_gb,
                }
            )

        info["gpu"] = gpu_list

    os_info = powershell_json(
        """
        $x = Get-CimInstance Win32_OperatingSystem
        [PSCustomObject]@{
            Caption = $x.Caption
            Version = $x.Version
            Build = $x.BuildNumber
        } | ConvertTo-Json -Compress
        """
    )

    if isinstance(os_info, dict):
        info["windows_edition"] = os_info.get(
            "Caption",
            "Unknown",
        )

        info["windows_build"] = os_info.get(
            "Build",
            "Unknown",
        )

    return info

# ============================================================
# BATTERY STATUS
# ============================================================
def get_battery_status():
    data = powershell_json(
        """
        $battery = Get-CimInstance Win32_Battery

        if (-not $battery) {
            [PSCustomObject]@{
                Present = $false
            } | ConvertTo-Json -Compress
            exit
        }

        $b = $battery | Select-Object -First 1

        $state = switch ($b.BatteryStatus) {
            1 { 'Discharging' }
            2 { 'AC / Charging' }
            3 { 'Fully Charged' }
            4 { 'Low' }
            5 { 'Critical' }
            6 { 'Charging' }
            7 { 'Charging / High' }
            8 { 'Charging / Low' }
            9 { 'Charging / Critical' }
            10 { 'Undefined' }
            11 { 'Partially Charged' }
            default { 'Unknown' }
        }

        [PSCustomObject]@{
            Present = $true
            ChargePercent = $b.EstimatedChargeRemaining
            Status = $state
            EstimatedRunTimeMinutes = $b.EstimatedRunTime
        } | ConvertTo-Json -Compress
        """
    )

    if not isinstance(data, dict):
        return {
            "present": False,
            "message": "Battery information unavailable.",
        }

    if not data.get("Present", False):
        return {
            "present": False,
            "message": "No battery detected.",
        }

    return {
        "present": True,
        "charge_percent": data.get(
            "ChargePercent",
            "Unknown",
        ),
        "status": data.get(
            "Status",
            "Unknown",
        ),
        "estimated_runtime_minutes": data.get(
            "EstimatedRunTimeMinutes",
            "Unknown",
        ),
    }

# ============================================================
# APPROVED APPS ONLY
# ============================================================
APP_COMMANDS = {
    # Browsers
    "edge": ["msedge.exe"],
    "microsoft edge": ["msedge.exe"],
    "brave": ["brave.exe"],
    "brave browser": ["brave.exe"],
    "chrome": ["chrome.exe"],
    "google chrome": ["chrome.exe"],

    # Windows / development
    "file explorer": ["explorer.exe"],
    "explorer": ["explorer.exe"],
    "vs code": ["code"],
    "visual studio code": ["code"],
    "vscode": ["code"],
    "arduino ide": ["arduino-ide.exe"],
    "arduino": ["arduino-ide.exe"],
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "calc": ["calc.exe"],
    "remote desktop": ["mstsc.exe"],
    "remote desktop connection": ["mstsc.exe"],
    "onenote": ["onenote.exe"],

    # Media / creation
    "vlc": ["vlc.exe"],
    "vlc media player": ["vlc.exe"],
    "handbrake": ["HandBrake.exe"],

    # Gaming / utilities
    "steam": ["steam.exe"],
    "razer cortex": ["RazerCortex.exe"],
    "rainmeter": ["Rainmeter.exe"],
}

def open_allowed_app(app_name):
    app_name = str(app_name).strip().lower()

    if app_name not in APP_COMMANDS:
        return (
            False,
            "That app is not in Megatron's allowed app list.",
        )

    try:
        subprocess.Popen(
            APP_COMMANDS[app_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            ),
        )

        return True, f"{app_name} opened successfully."

    except FileNotFoundError:
        return (
            False,
            f"{app_name} is not available from the current Windows PATH.",
        )

    except Exception as error:
        return (
            False,
            f"Could not open {app_name}: {error}",
        )


# ============================================================
# SMART APP LAUNCHER / CLOSER
# ============================================================
APP_ALIASES = {
    "edge": "microsoft edge",
    "microsoft edge": "microsoft edge",
    "brave": "brave",
    "brave browser": "brave",
    "chrome": "google chrome",
    "google chrome": "google chrome",
    "browser": "google chrome",
    "file explorer": "file explorer",
    "explorer": "file explorer",
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "visual studio code": "visual studio code",
    "arduino": "arduino ide",
    "arduino ide": "arduino ide",
    "notepad": "notepad",
    "calculator": "calculator",
    "calc": "calculator",
    "remote desktop": "remote desktop connection",
    "remote desktop connection": "remote desktop connection",
    "onenote": "onenote",
    "vlc": "vlc media player",
    "vlc media player": "vlc media player",
    "handbrake": "handbrake",
    "steam": "steam",
    "razer": "razer cortex",
    "razer cortex": "razer cortex",
    "rainmeter": "rainmeter",
}

APP_PROCESS_NAMES = {
    "microsoft edge": "msedge.exe",
    "google chrome": "chrome.exe",
    "file explorer": "explorer.exe",
    "visual studio code": "Code.exe",
    "arduino ide": "arduino-ide.exe",
    "notepad": "notepad.exe",
    "calculator": "CalculatorApp.exe",
}

def normalize_app_name(value):
    key = " ".join(str(value or "").strip().lower().split())
    return APP_ALIASES.get(key, key)

def close_allowed_app(app_name):
    normalized = normalize_app_name(app_name)
    process_name = APP_PROCESS_NAMES.get(normalized)

    if not process_name:
        return False, "That app is not in Megatron's safe app list."

    # Avoid killing explorer.exe because it is the Windows shell.
    if process_name.lower() == "explorer.exe":
        return False, "I won't close File Explorer through this command because explorer.exe is the Windows shell."

    try:
        result = subprocess.run(
            [
                "taskkill",
                "/IM",
                process_name,
                "/T",
                "/F",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            ),
        )

        output = (result.stdout + " " + result.stderr).strip()
        low = output.lower()

        if "not found" in low or "no running instance" in low:
            return False, f"{normalized.title()} is not currently running."

        if result.returncode == 0:
            return True, f"{normalized.title()} closed."

        return False, f"Could not close {normalized.title()}: {output or 'unknown error'}"

    except Exception as error:
        return False, f"Could not close {normalized.title()}: {error}"


def execute_app_control(arguments):
    action = " ".join(
        str(arguments.get("action", "")).strip().lower().split()
    )
    target = normalize_app_name(arguments.get("target", ""))

    if action in {"open", "launch", "start"}:
        if not target:
            return {
                "content": [{
                    "type": "text",
                    "text": "Please specify which app to open.",
                }],
                "isError": True,
            }

        success, message = open_allowed_app(target)
        return {
            "content": [{
                "type": "text",
                "text": message,
            }],
            "isError": not success,
        }

    if action in {"close", "quit", "exit", "stop"}:
        if not target:
            return {
                "content": [{
                    "type": "text",
                    "text": "Please specify which app to close.",
                }],
                "isError": True,
            }

        success, message = close_allowed_app(target)
        return {
            "content": [{
                "type": "text",
                "text": message,
            }],
            "isError": not success,
        }

    return {
        "content": [{
            "type": "text",
            "text": f"Unknown app control action: {action or 'none'}",
        }],
        "isError": True,
    }


# ============================================================
# MEDIA KEY CONTROL
# ============================================================
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002

def press_media_key(vk_code):
    user32 = ctypes.windll.user32

    user32.keybd_event(
        vk_code,
        0,
        0,
        0,
    )

    user32.keybd_event(
        vk_code,
        0,
        KEYEVENTF_KEYUP,
        0,
    )

# ============================================================
# SET VOLUME VIA PYCAW
# ============================================================
def set_volume_percent(percent):
    try:
        from pycaw.pycaw import (
            AudioUtilities,
            IAudioEndpointVolume,
        )
        from comtypes import CLSCTX_ALL

        devices = AudioUtilities.GetSpeakers()

        interface = devices.Activate(
            IAudioEndpointVolume._iid_,
            CLSCTX_ALL,
            None,
        )

        volume = interface.QueryInterface(
            IAudioEndpointVolume
        )

        value = max(
            0.0,
            min(
                1.0,
                float(percent) / 100.0,
            ),
        )

        volume.SetMasterVolumeLevelScalar(
            value,
            None,
        )

        return True, f"Volume set to {float(percent):g}%."

    except Exception as error:
        return (
            False,
            "set_volume needs pycaw/comtypes. Install with: "
            "python -m pip install pycaw comtypes. "
            f"Current error: {error}",
        )

# ============================================================
# PC HEALTH
# ============================================================
def get_pc_health():
    """Return a compact, voice-friendly Windows PC health snapshot."""
    health = {
        "cpu_percent": "Unknown",
        "ram_percent": "Unknown",
        "ram_used_gb": "Unknown",
        "ram_total_gb": "Unknown",
        "disk_free_gb": "Unknown",
        "disk_total_gb": "Unknown",
        "uptime": "Unknown",
        "internet": "Unknown",
        "battery": None,
        "gpu": "Unknown",
    }

    system = powershell_json(r"""
        $os = Get-CimInstance Win32_OperatingSystem
        $cpus = @(Get-CimInstance Win32_Processor)
        $cpuLoad = ($cpus | Measure-Object -Property LoadPercentage -Average).Average
        $totalKb = [double]$os.TotalVisibleMemorySize
        $freeKb = [double]$os.FreePhysicalMemory
        $usedKb = $totalKb - $freeKb
        $boot = $os.LastBootUpTime
        $uptimeSeconds = ((Get-Date) - $boot).TotalSeconds
        $disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
        $gpu = @(Get-CimInstance Win32_VideoController | Where-Object { $_.Name -and $_.Name -notmatch 'Microsoft Basic' })
        [PSCustomObject]@{
            CpuPercent = [math]::Round($cpuLoad, 1)
            RamPercent = if ($totalKb -gt 0) { [math]::Round(($usedKb / $totalKb) * 100, 1) } else { $null }
            RamUsedGB = [math]::Round($usedKb / 1MB, 2)
            RamTotalGB = [math]::Round($totalKb / 1MB, 2)
            DiskFreeGB = if ($disk) { [math]::Round([double]$disk.FreeSpace / 1GB, 2) } else { $null }
            DiskTotalGB = if ($disk) { [math]::Round([double]$disk.Size / 1GB, 2) } else { $null }
            UptimeSeconds = [math]::Round($uptimeSeconds, 0)
            Gpu = if ($gpu.Count -gt 0) { ($gpu | Select-Object -First 1).Name } else { $null }
        } | ConvertTo-Json -Compress
    """)

    if isinstance(system, dict):
        mapping = {
            "CpuPercent": "cpu_percent",
            "RamPercent": "ram_percent",
            "RamUsedGB": "ram_used_gb",
            "RamTotalGB": "ram_total_gb",
            "DiskFreeGB": "disk_free_gb",
            "DiskTotalGB": "disk_total_gb",
            "Gpu": "gpu",
        }
        for key, target in mapping.items():
            value = system.get(key)
            if value is not None:
                health[target] = value

        seconds = system.get("UptimeSeconds")
        if isinstance(seconds, (int, float)):
            days, rem = divmod(int(seconds), 86400)
            hours, rem = divmod(rem, 3600)
            minutes, _ = divmod(rem, 60)
            parts = []
            if days:
                parts.append(f"{days}d")
            if hours:
                parts.append(f"{hours}h")
            if minutes or not parts:
                parts.append(f"{minutes}m")
            health["uptime"] = " ".join(parts)

    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "Test-Connection -ComputerName 1.1.1.1 -Count 1 -Quiet",
            ],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        health["internet"] = (
            "Connected"
            if result.stdout.strip().lower() == "true"
            else "Offline"
        )
    except Exception:
        health["internet"] = "Unknown"

    health["battery"] = get_battery_status()
    return health

# ============================================================
# SMART FILE FINDER
# ============================================================
FILE_SEARCH_ROOTS = [
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    Path.home() / "Documents",
    PROJECT_DIR,
]

FILE_SEARCH_SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
}

def _iter_search_roots():
    seen = set()

    for root in FILE_SEARCH_ROOTS:
        try:
            root = root.resolve()
        except Exception:
            continue

        key = str(root).lower()

        if key in seen or not root.exists():
            continue

        seen.add(key)
        yield root

def find_local_files(query, file_type="any", max_results=20):
    query = str(query or "").strip().lower()

    extensions = None

    if file_type and str(file_type).lower() not in {"any", "all"}:
        kind = str(file_type).lower().strip()

        extension_groups = {
            "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"},
            "images": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"},
            "audio": {".mp3", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".flac"},
            "music": {".mp3", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".flac"},
            "video": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv"},
            "document": {".pdf", ".doc", ".docx", ".txt", ".rtf"},
            "documents": {".pdf", ".doc", ".docx", ".txt", ".rtf"},
            "code": {".py", ".js", ".ts", ".cpp", ".c", ".h", ".html", ".css", ".json"},
        }

        if kind in extension_groups:
            extensions = extension_groups[kind]
        elif kind.startswith("."):
            extensions = {kind}

    results = []

    for root in _iter_search_roots():
        try:
            for current_root, dirs, files in os.walk(root):
                dirs[:] = [
                    d for d in dirs
                    if d not in FILE_SEARCH_SKIP_DIRS
                    and not d.startswith(".")
                ]

                for filename in files:
                    path = Path(current_root) / filename
                    lower_name = filename.lower()

                    if extensions is not None and path.suffix.lower() not in extensions:
                        continue

                    if query:
                        stem = path.stem.lower()
                        if query not in lower_name and query not in stem:
                            continue

                    try:
                        mtime = path.stat().st_mtime
                    except OSError:
                        mtime = 0

                    results.append((mtime, path))

                    if len(results) >= 500:
                        break

                if len(results) >= 500:
                    break

        except (PermissionError, OSError):
            continue

        if len(results) >= 500:
            break

    results.sort(key=lambda item: item[0], reverse=True)

    return [path for _, path in results[:max_results]]

def execute_file_finder(arguments):
    query = str(arguments.get("query", "")).strip()
    file_type = str(arguments.get("file_type", "any")).strip()
    action = str(arguments.get("action", "find")).strip().lower()

    if action in {"find", "search", "locate"}:
        results = find_local_files(
            query,
            file_type=file_type,
            max_results=20,
        )

        if not results:
            return {
                "content": [{
                    "type": "text",
                    "text": f"No files found for '{query}'.",
                }],
                "isError": False,
            }

        lines = [
            f"{index}. {path}"
            for index, path in enumerate(results, start=1)
        ]

        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Found {len(results)} file(s):\n"
                    + "\n".join(lines)
                ),
            }]
        }

    if action in {"open", "open_first", "launch"}:
        results = find_local_files(
            query,
            file_type=file_type,
            max_results=1,
        )

        if not results:
            return {
                "content": [{
                    "type": "text",
                    "text": f"No file found for '{query}'.",
                }],
                "isError": True,
            }

        target = results[0]

        try:
            os.startfile(str(target))
            return {
                "content": [{
                    "type": "text",
                    "text": f"Opened: {target}",
                }]
            }
        except Exception as error:
            return {
                "content": [{
                    "type": "text",
                    "text": f"Could not open file: {error}",
                }],
                "isError": True,
            }

    return {
        "content": [{
            "type": "text",
            "text": f"Unknown file finder action: {action}",
        }],
        "isError": True,
    }

# ============================================================
# PC CONTROL
# ============================================================
def get_active_window_title():
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "Unknown"
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return "Unknown"
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value or "Unknown"
    except Exception:
        return "Unknown"


def press_key_combo(keys):
    if os.name != "nt":
        raise RuntimeError("Keyboard control requires Windows.")

    user32 = ctypes.windll.user32
    vk_map = {
        "ctrl": 0x11, "control": 0x11, "shift": 0x10,
        "alt": 0x12, "win": 0x5B, "windows": 0x5B,
        "enter": 0x0D, "escape": 0x1B, "esc": 0x1B,
        "tab": 0x09, "space": 0x20, "backspace": 0x08,
        "delete": 0x2E, "del": 0x2E, "home": 0x24,
        "end": 0x23, "left": 0x25, "up": 0x26,
        "right": 0x27, "down": 0x28, "pageup": 0x21,
        "pagedown": 0x22,
    }
    for ch in "abcdefghijklmnopqrstuvwxyz":
        vk_map[ch] = ord(ch.upper())
    for ch in "0123456789":
        vk_map[ch] = ord(ch)

    normalized = [str(k).strip().lower() for k in keys if str(k).strip()]
    if not normalized:
        raise ValueError("No keys were provided.")

    vk_codes = []
    for key in normalized:
        if key.startswith("f") and key[1:].isdigit():
            number = int(key[1:])
            if 1 <= number <= 12:
                vk_codes.append(0x70 + number - 1)
                continue
        if key not in vk_map:
            raise ValueError(f"Unsupported key: {key}")
        vk_codes.append(vk_map[key])

    for vk in vk_codes:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vk_codes):
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def move_mouse(x, y):
    if os.name != "nt":
        raise RuntimeError("Mouse control requires Windows.")
    user32 = ctypes.windll.user32
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    x = max(0, min(int(x), width - 1))
    y = max(0, min(int(y), height - 1))
    user32.SetCursorPos(x, y)


def mouse_button(button):
    if os.name != "nt":
        raise RuntimeError("Mouse control requires Windows.")
    user32 = ctypes.windll.user32
    flags = {
        "left": (0x0002, 0x0004),
        "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
    }
    name = str(button).strip().lower()
    if name not in flags:
        raise ValueError("Mouse button must be left, right, or middle.")
    down, up = flags[name]
    user32.mouse_event(down, 0, 0, 0, 0)
    user32.mouse_event(up, 0, 0, 0, 0)


def take_screenshot():
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"screenshot_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    output = SCREENSHOT_DIR / filename
    try:
        from PIL import ImageGrab
        image = ImageGrab.grab()
        image.save(output, "PNG")
        return True, str(output)
    except ImportError:
        return False, "Screenshot requires Pillow. Install with: python -m pip install Pillow"
    except Exception as error:
        return False, f"Screenshot failed: {error}"


def open_folder_target(target):
    target = str(target).strip()
    lower = target.lower()
    known = {
        "desktop": Path.home() / "Desktop",
        "desktop folder": Path.home() / "Desktop",
        "downloads": Path.home() / "Downloads",
        "downloads folder": Path.home() / "Downloads",
        "documents": Path.home() / "Documents",
        "documents folder": Path.home() / "Documents",
    }
    path = known.get(lower, Path(target).expanduser())
    if not path.exists():
        return False, f"Path not found: {path}"
    try:
        os.startfile(str(path))
        return True, f"Opened {path}"
    except Exception as error:
        return False, f"Could not open {path}: {error}"


def execute_pc_control(arguments):
    action = str(arguments.get("action", "")).strip().lower()
    action = {
        "take_screenshot": "screenshot",
        "maximize_window": "maximize",
        "minimize_window": "minimize",
        "close_window": "close",
        "switch_window": "switch window",
        "keypress": "press_key",
        "press_keys": "press_key",
        "key_press": "press_key",
        "press": "press_key",
    }.get(action, action)
    if not action:
        return {"content": [{"type": "text", "text": "No PC control action was provided."}], "isError": True}

    try:
        if action in {"screenshot", "take screenshot", "capture screenshot"}:
            success, result = take_screenshot()
            return {"content": [{"type": "text", "text": f"Screenshot saved: {result}" if success else result}], "isError": not success}

        if action == "open_app":
            success, message = open_allowed_app(arguments.get("target", ""))
            return {"content": [{"type": "text", "text": message}], "isError": not success}

        if action == "open_folder":
            success, message = open_folder_target(arguments.get("target", ""))
            return {"content": [{"type": "text", "text": message}], "isError": not success}

        if action in {"minimize", "minimize window"}:
            press_key_combo(["win", "down"])
            return {"content": [{"type": "text", "text": "Window minimized."}]}

        if action in {"maximize", "maximize window"}:
            press_key_combo(["win", "up"])
            return {"content": [{"type": "text", "text": "Window maximized."}]}

        if action in {"close", "close window"}:
            press_key_combo(["alt", "f4"])
            return {"content": [{"type": "text", "text": "Close command sent to the current window."}]}

        if action in {"switch window", "switch windows", "alt tab"}:
            press_key_combo(["alt", "tab"])
            return {"content": [{"type": "text", "text": "Switched window."}]}

        if action in {"press_key", "key", "keyboard"}:
            keys = arguments.get("keys", [])
            if isinstance(keys, str):
                keys = [part.strip() for part in keys.split("+")]
            press_key_combo(keys)
            return {"content": [{"type": "text", "text": "Key command executed."}]}

        if action == "move_mouse":
            x = arguments.get("x")
            y = arguments.get("y")
            if x is None or y is None:
                raise ValueError("move_mouse requires x and y.")
            move_mouse(x, y)
            return {"content": [{"type": "text", "text": f"Mouse moved to {int(x)}, {int(y)}."}]}

        if action in {"left_click", "right_click", "middle_click", "click"}:
            button = action.replace("_click", "") if action != "click" else arguments.get("button", "left")
            mouse_button(button)
            return {"content": [{"type": "text", "text": f"{str(button).title()} click executed."}]}

        if action in {"double_click", "double click"}:
            mouse_button("left")
            time.sleep(0.08)
            mouse_button("left")
            return {"content": [{"type": "text", "text": "Double click executed."}]}

        if action == "open_file":
            target = str(arguments.get("target", "")).strip()
            if not target:
                raise ValueError("open_file requires a target path.")
            path = Path(target).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"File not found: {path}")
            os.startfile(str(path))
            return {"content": [{"type": "text", "text": f"Opened: {path}"}]}

        if action in {"active_window", "current_window"}:
            return {"content": [{"type": "text", "text": f"Active window: {get_active_window_title()}"}]}

        return {"content": [{"type": "text", "text": f"Unknown PC control action: {action}"}], "isError": True}
    except Exception as error:
        return {"content": [{"type": "text", "text": f"PC control failed: {error}"}], "isError": True}

# ============================================================

# ============================================================
# SMART MATH BRAIN
# ============================================================
def math_brain(request):
    """Handle common real-world math, percentage, unit, and date requests."""
    import math
    import re
    from datetime import date

    text = " ".join(str(request).strip().lower().split())

    if not text:
        return False, "Please provide a math problem."

    # Normalize common spoken/symbol forms.
    normalized = (
        text.replace("×", "*")
        .replace("÷", "/")
        .replace("^", "**")
    )

    # Percentage of a number: 25% of 840.
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%\s*(?:of|in)\s*(-?\d+(?:\.\d+)?)", normalized)
    if m:
        pct = float(m.group(1))
        number = float(m.group(2))
        result = number * pct / 100
        return True, f"{pct:g}% of {number:g} = {result:g}"

    # Percentage change: from A to B / increase/decrease from A to B.
    m = re.search(
        r"(?:from\s*)?(-?\d+(?:\.\d+)?)\s*(?:to)\s*(-?\d+(?:\.\d+)?)",
        normalized,
    )
    if m and any(k in normalized for k in ("percent change", "percentage change", "increase", "decrease", "change")):
        old = float(m.group(1))
        new = float(m.group(2))
        if old == 0:
            return True, "Percentage change is undefined when the starting value is 0."
        change = (new - old) / abs(old) * 100
        direction = "increase" if change >= 0 else "decrease"
        return True, f"{abs(change):g}% {direction}."

    # Feet/inches to centimeters. Supports 5 feet 8 inches / 5'8".
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:feet|foot|ft|')\s*(?:(\d+(?:\.\d+)?)\s*(?:inches|inch|in|\"))?", normalized)
    if m:
        feet = float(m.group(1))
        inches = float(m.group(2) or 0)
        cm = (feet * 12 + inches) * 2.54
        return True, f"{feet:g} feet {inches:g} inches = {cm:.2f} cm."

    # Simple unit conversions.
    unit_patterns = [
        (r"(-?\d+(?:\.\d+)?)\s*(?:km|kilometers?)\s*(?:to|in)\s*(?:miles?|mi)", lambda x: x * 0.621371, "miles"),
        (r"(-?\d+(?:\.\d+)?)\s*(?:miles?|mi)\s*(?:to|in)\s*(?:km|kilometers?)", lambda x: x / 0.621371, "km"),
        (r"(-?\d+(?:\.\d+)?)\s*(?:kg|kilograms?)\s*(?:to|in)\s*(?:lb|lbs|pounds?)", lambda x: x * 2.2046226218, "lb"),
        (r"(-?\d+(?:\.\d+)?)\s*(?:lb|lbs|pounds?)\s*(?:to|in)\s*(?:kg|kilograms?)", lambda x: x / 2.2046226218, "kg"),
        (r"(-?\d+(?:\.\d+)?)\s*(?:meters?|m)\s*(?:to|in)\s*(?:feet|ft)", lambda x: x * 3.280839895, "ft"),
        (r"(-?\d+(?:\.\d+)?)\s*(?:feet|ft)\s*(?:to|in)\s*(?:meters?|m)", lambda x: x / 3.280839895, "m"),
        (r"(-?\d+(?:\.\d+)?)\s*(?:liters?|l)\s*(?:to|in)\s*(?:gallons?|gal)", lambda x: x * 0.2641720524, "US gal"),
        (r"(-?\d+(?:\.\d+)?)\s*(?:gallons?|gal)\s*(?:to|in)\s*(?:liters?|l)", lambda x: x / 0.2641720524, "L"),
    ]
    for pattern, converter, unit in unit_patterns:
        m = re.search(pattern, normalized)
        if m:
            value = float(m.group(1))
            return True, f"{value:g} = {converter(value):.4g} {unit}."

    # Temperature.
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?(c|celsius)\s*(?:to|in)\s*(?:°\s*)?(f|fahrenheit)", normalized)
    if m:
        c = float(m.group(1))
        f = c * 9 / 5 + 32
        return True, f"{c:g}°C = {f:g}°F."
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?(f|fahrenheit)\s*(?:to|in)\s*(?:°\s*)?(c|celsius)", normalized)
    if m:
        f = float(m.group(1))
        c = (f - 32) * 5 / 9
        return True, f"{f:g}°F = {c:g}°C."

    # Time conversions.
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:(\d+(?:\.\d+)?)\s*(?:minutes?|mins?))?\s*(?:to|in)\s*(?:seconds?|sec)", normalized)
    if m:
        hours = float(m.group(1))
        minutes = float(m.group(2) or 0)
        seconds = hours * 3600 + minutes * 60
        return True, f"{hours:g} hours {minutes:g} minutes = {seconds:g} seconds."

    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:to|in)\s*(?:minutes?|mins?)", normalized)
    if m:
        hours = float(m.group(1))
        return True, f"{hours:g} hours = {hours * 60:g} minutes."

    # Lakh/crore number conversion.
    m = re.search(r"(\d+(?:\.\d+)?)\s*lakh\s*(?:to|in)\s*(million|mn)", normalized)
    if m:
        lakh = float(m.group(1))
        return True, f"{lakh:g} lakh = {lakh * 0.1:g} million."

    m = re.search(r"(\d+(?:\.\d+)?)\s*crore\s*(?:to|in)\s*(million|mn)", normalized)
    if m:
        crore = float(m.group(1))
        return True, f"{crore:g} crore = {crore:g} million."

    # Common spoken arithmetic.
    expr = normalized
    replacements = {
        "plus": "+",
        "minus": "-",
        "times": "*",
        "multiplied by": "*",
        "divided by": "/",
        "over": "/",
    }
    for word, symbol in replacements.items():
        expr = expr.replace(word, symbol)
    expr = expr.replace("what is", "").replace("calculate", "").strip()

    if re.fullmatch(r"[0-9\s+\-*/().%]+", expr):
        try:
            result = safe_calculate(expr.replace("%", "/100"))
            return True, f"{expr} = {result:g}" if isinstance(result, float) and result.is_integer() else f"{expr} = {result}"
        except Exception:
            pass

    # Basic square root / power request.
    m = re.search(r"(?:square root|sqrt)\s*(?:of)?\s*(\d+(?:\.\d+)?)", normalized)
    if m:
        value = float(m.group(1))
        return True, f"The square root of {value:g} is {math.sqrt(value):.6g}."

    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:to the power of|power)\s*(\d+(?:\.\d+)?)", normalized)
    if m:
        base = float(m.group(1))
        exponent = float(m.group(2))
        return True, f"{base:g} to the power of {exponent:g} = {base ** exponent:g}."

    return False, "I couldn't identify a supported math operation."


# ============================================================
# SMART CONVERTER
# ============================================================
def smart_converter(request):
    """Dedicated converter for English/Hinglish/Hindi unit requests."""
    import re

    text = " ".join(str(request or "").strip().lower().split())
    if not text:
        return False, "Please provide a conversion request."

    # Normalize common Hindi/Hinglish wording into tokens understood by math_brain.
    replacements = {
        "किलोमीटर": "km",
        "किलोमीटरों": "km",
        "किमी": "km",
        "मील": "miles",
        "किलोग्राम": "kg",
        "किलो": "kg",
        "पाउंड": "pounds",
        "फीट": "feet",
        "फुट": "feet",
        "इंच": "inches",
        "सेंटीमीटर": "cm",
        "मीटर": "meters",
        "लीटर": "liters",
        "गैलन": "gallons",
        "सेल्सियस": "celsius",
        "फॉरेनहाइट": "fahrenheit",
        "डिग्री सेल्सियस": "celsius",
        "डिग्री फॉरेनहाइट": "fahrenheit",
        "घंटे": "hours",
        "घंटा": "hour",
        "मिनट": "minutes",
        "मिनटों": "minutes",
        "सेकंड": "seconds",
        "सेकंड्स": "seconds",
        "से": "to",
        "में": "to",
        "को": "to",
        "बदलें": "convert",
        "बदलो": "convert",
        "कन्वर्ट": "convert",
        "करो": "convert",
        "में कन्वर्ट करो": "convert",
        "में बदलो": "convert",
        "करना है": "convert",
    }

    normalized = text
    # Longer phrases first to avoid partial substitutions.
    for src in sorted(replacements, key=len, reverse=True):
        normalized = normalized.replace(src, replacements[src])

    normalized = " ".join(normalized.split())

    # Currency: delegate live exchange-rate lookup to the existing web tool.
    currency_words = (
        "inr", "rupee", "rupees", "₹", "usd", "dollar", "dollars", "$",
        "eur", "euro", "euros", "€", "gbp", "pound sterling", "£",
        "jpy", "yen", "cny", "yuan", "aed", "cad", "aud",
    )
    if sum(1 for token in currency_words if token in normalized) >= 2:
        return True, "CURRENCY_LIVE_SEARCH|" + normalized

    # Convert common Hindi numeric words used around units.
    normalized = normalized.replace("एक किलोमीटर", "1 km")
    normalized = normalized.replace("दो किलोमीटर", "2 km")
    normalized = normalized.replace("दस किलोमीटर", "10 km")

    # Strip conversational filler that can confuse the math engine.
    normalized = re.sub(r"\b(?:please|please convert|convert|conversion|can you|could you|tell me|what is|what's)\b", " ", normalized)
    normalized = " ".join(normalized.split())

    handled, result = math_brain(normalized)
    if handled:
        return True, result

    # Direct regex fallback for the most common unit pairs.
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*km\s*(?:to|in)\s*miles?", normalized)
    if m:
        value = float(m.group(1))
        return True, f"{value:g} km = {value * 0.621371:.4f} miles."

    m = re.search(r"(-?\d+(?:\.\d+)?)\s*miles?\s*(?:to|in)\s*km", normalized)
    if m:
        value = float(m.group(1))
        return True, f"{value:g} miles = {value / 0.621371:.4f} km."

    m = re.search(r"(-?\d+(?:\.\d+)?)\s*kg\s*(?:to|in)\s*(?:pounds?|lb)", normalized)
    if m:
        value = float(m.group(1))
        return True, f"{value:g} kg = {value * 2.2046226218:.4f} pounds."

    m = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:pounds?|lb)\s*(?:to|in)\s*kg", normalized)
    if m:
        value = float(m.group(1))
        return True, f"{value:g} pounds = {value / 2.2046226218:.4f} kg."

    m = re.search(r"(-?\d+(?:\.\d+)?)\s*cm\s*(?:to|in)\s*(?:meters?|m)", normalized)
    if m:
        value = float(m.group(1))
        return True, f"{value:g} cm = {value / 100:.4f} meters."

    return False, "I couldn't identify a supported conversion."

# ============================================================
# SMART REMINDERS
# ============================================================
REMINDERS_FILE = PROJECT_DIR / "megatron_reminders.json"


def reminder_local_now():
    """Use the Windows machine's local clock without requiring tzdata."""
    return datetime.datetime.now()


def normalize_reminder_datetime(value):
    """Convert aware/naive reminder timestamps to naive local time."""
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


def load_reminders():
    try:
        if not REMINDERS_FILE.exists():
            return []
        data = json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_reminders(reminders):
    REMINDERS_FILE.write_text(
        json.dumps(reminders, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_reminder_due(args):
    now = reminder_local_now()

    if args.get("delay_minutes") is not None:
        mins = float(args["delay_minutes"])
        if mins <= 0:
            raise ValueError("delay_minutes must be greater than 0")
        return now + datetime.timedelta(minutes=mins)

    if args.get("delay_seconds") is not None:
        secs = float(args["delay_seconds"])
        if secs <= 0:
            raise ValueError("delay_seconds must be greater than 0")
        return now + datetime.timedelta(seconds=secs)

    raw = str(args.get("remind_at", "")).strip()
    if not raw:
        raise ValueError("Provide delay_minutes, delay_seconds, or remind_at")

    parsed = None
    for fmt in ("%H:%M", "%I:%M %p", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.datetime.strptime(raw, fmt)
            if fmt != "%Y-%m-%d %H:%M":
                parsed = parsed.replace(
                    year=now.year,
                    month=now.month,
                    day=now.day,
                )
            break
        except ValueError:
            pass

    if parsed is None:
        try:
            parsed = datetime.datetime.fromisoformat(
                raw.replace("Z", "+00:00")
            )
        except ValueError as error:
            raise ValueError(
                "Use a time like 19:00, 7:00 PM, or an ISO date-time"
            ) from error

    parsed = normalize_reminder_datetime(parsed)

    if parsed <= now:
        if len(raw) <= 8 and ":" in raw:
            parsed += datetime.timedelta(days=1)
        else:
            raise ValueError("Reminder time must be in the future")

    return parsed


def add_reminder(args):
    text = " ".join(str(args.get("text", "")).strip().split())
    if not text:
        return {
            "content": [{
                "type": "text",
                "text": "Tell me what I should remind you about.",
            }],
            "isError": True,
        }

    try:
        due = parse_reminder_due(args)
    except Exception as error:
        return {
            "content": [{
                "type": "text",
                "text": f"Reminder time error: {error}",
            }],
            "isError": True,
        }

    reminders = load_reminders()
    rid = max(
        [int(r.get("id", 0)) for r in reminders] or [0]
    ) + 1

    reminders.append({
        "id": rid,
        "text": text,
        "due_at": due.isoformat(),
        "done": False,
    })
    save_reminders(reminders)

    label = due.strftime(
        "%I:%M %p IST | %d %b %Y"
    )

    return {
        "content": [{
            "type": "text",
            "text": f"Reminder #{rid} set for {label}: {text}",
        }]
    }


def list_reminders():
    rows = [
        r for r in load_reminders()
        if not r.get("done")
    ]

    if not rows:
        return {
            "content": [{
                "type": "text",
                "text": "You have no active reminders.",
            }]
        }

    lines = []
    for reminder in sorted(
        rows,
        key=lambda item: item.get("due_at", ""),
    ):
        try:
            due = normalize_reminder_datetime(
                datetime.datetime.fromisoformat(
                    reminder["due_at"]
                )
            )
            when = due.strftime(
                "%I:%M %p IST | %d %b %Y"
            )
        except Exception:
            when = reminder.get("due_at", "unknown")

        lines.append(
            f"#{reminder['id']} — {when} — {reminder['text']}"
        )

    return {
        "content": [{
            "type": "text",
            "text": "Active reminders:\n" + "\n".join(lines),
        }]
    }


def cancel_reminder(args):
    reminders = load_reminders()
    rid = args.get("id")
    query = " ".join(
        str(args.get("query", "")).lower().split()
    )

    if rid is not None:
        try:
            rid = int(rid)
        except Exception:
            return {
                "content": [{
                    "type": "text",
                    "text": "Reminder id must be a number.",
                }],
                "isError": True,
            }

        for reminder in reminders:
            if (
                int(reminder.get("id", -1)) == rid
                and not reminder.get("done")
            ):
                reminder["done"] = True
                save_reminders(reminders)
                return {
                    "content": [{
                        "type": "text",
                        "text": f"Cancelled reminder #{rid}.",
                    }]
                }

        return {
            "content": [{
                "type": "text",
                "text": f"No active reminder #{rid} found.",
            }],
            "isError": True,
        }

    if query:
        for reminder in reminders:
            if (
                not reminder.get("done")
                and query in reminder.get("text", "").lower()
            ):
                reminder["done"] = True
                save_reminders(reminders)
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            f"Cancelled reminder #{reminder['id']}: "
                            f"{reminder['text']}"
                        ),
                    }]
                }

    return {
        "content": [{
            "type": "text",
            "text": "I couldn't find that active reminder.",
        }],
        "isError": True,
    }


def execute_reminder(args):
    action = str(
        args.get("action", "add")
    ).strip().lower()

    if action in {"add", "create", "set", "remind"}:
        return add_reminder(args)
    if action in {"list", "show", "view"}:
        return list_reminders()
    if action in {"cancel", "delete", "remove"}:
        return cancel_reminder(args)

    return {
        "content": [{
            "type": "text",
            "text": "Reminder action must be add, list, or cancel.",
        }],
        "isError": True,
    }


def notify_reminder(text):
    """Show a Windows-native interactive reminder popup."""
    message = str(text).replace("'", "''")
    title = "Megatron Reminder"

    powershell_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        f"[System.Windows.Forms.MessageBox]::Show('{message}'," 
        f"'{title}', 'OK', 'Information') | Out-Null"
    )

    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                powershell_script,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            ),
        )
    except Exception as error:
        print(
            "Reminder notification failed:",
            error,
        )


async def reminder_worker():
    while True:
        try:
            now = reminder_local_now()
            reminders = load_reminders()
            changed = False

            for reminder in reminders:
                if reminder.get("done"):
                    continue

                try:
                    due = normalize_reminder_datetime(
                        datetime.datetime.fromisoformat(
                            reminder["due_at"]
                        )
                    )
                except Exception:
                    continue

                if due <= now:
                    print(
                        f"\nREMINDER DUE: "
                        f"{reminder.get('text', '')}"
                    )
                    play_effect("reminder")
                    notify_reminder(
                        reminder.get("text", "")
                    )
                    reminder["done"] = True
                    changed = True

            if changed:
                save_reminders(reminders)

        except Exception as error:
            print(
                "Reminder worker error:",
                error,
            )

        await asyncio.sleep(2)

# ============================================================
# SMART QUIZ ENGINE
# ============================================================
QUIZ_STATE = {
    "active": False,
    "topic": "",
    "difficulty": "medium",
    "current_question": "",
    "current_answer": "",
    "current_explanation": "",
    "question_number": 0,
    "score": 0,
    "total": 0,
}


def _extract_json_object(text):
    text = str(text or "").strip()
    if not text:
        return None
    # Remove common markdown fences.
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
    return None


async def _generate_quiz_question(topic, difficulty, question_number):
    prompt = f"""
You are Megatron's Smart Quiz Engine.
Create exactly ONE educational multiple-choice question.
Return ONLY valid JSON with these keys:
question, options, answer, explanation
Where options is an array of exactly 4 strings, and answer is the exact option text.
Topic: {topic}
Difficulty: {difficulty}
Question number: {question_number}
Avoid trick questions and avoid ambiguous wording.
""".strip()
    result = await ask_real_megatron(prompt)
    if not isinstance(result, dict) or "content" not in result:
        return None, str(result)
    text = ""
    try:
        text = result["content"][0]["text"]
    except Exception:
        text = ""
    data = _extract_json_object(text)
    if not isinstance(data, dict):
        return None, "Quiz generator returned an invalid question format."
    options = data.get("options")
    answer = str(data.get("answer", "")).strip()
    question = str(data.get("question", "")).strip()
    explanation = str(data.get("explanation", "")).strip()
    if not question or not isinstance(options, list) or len(options) != 4 or not answer:
        return None, "Quiz generator returned an incomplete question."
    return {
        "question": question,
        "options": [str(x) for x in options],
        "answer": answer,
        "explanation": explanation,
    }, ""


async def smart_quiz(arguments):
    action = str(arguments.get("action", "start")).strip().lower()
    topic = str(arguments.get("topic", "general knowledge")).strip() or "general knowledge"
    difficulty = str(arguments.get("difficulty", "medium")).strip().lower() or "medium"
    count = int(arguments.get("count", 5) or 5)
    user_answer = str(arguments.get("answer", "")).strip()

    if difficulty not in {"easy", "medium", "hard", "expert"}:
        difficulty = "medium"
    count = max(1, min(count, 10))

    if action in {"stop", "cancel", "end"}:
        QUIZ_STATE.update({
            "active": False,
            "topic": "",
            "current_question": "",
            "current_answer": "",
            "current_explanation": "",
            "question_number": 0,
            "score": 0,
            "total": 0,
        })
        return {"content":[{"type":"text","text":"Quiz stopped."}]}

    if action == "score":
        return {"content":[{"type":"text","text":f"Current score: {QUIZ_STATE['score']} / {max(QUIZ_STATE['question_number']-1, 0)}."}]}

    if action in {"start", "restart", "next"}:
        if action in {"start", "restart"}:
            QUIZ_STATE.update({
                "active": True,
                "topic": topic,
                "difficulty": difficulty,
                "question_number": 1,
                "score": 0,
                "total": count,
            })
        elif not QUIZ_STATE.get("active"):
            return {"content":[{"type":"text","text":"No active quiz. Say start a quiz first."}],"isError":True}
        else:
            QUIZ_STATE["question_number"] += 1

        if QUIZ_STATE["question_number"] > QUIZ_STATE["total"]:
            score = QUIZ_STATE["score"]
            total = QUIZ_STATE["total"]
            QUIZ_STATE["active"] = False
            return {"content":[{"type":"text","text":f"Quiz complete! Score: {score}/{total}."}]}

        question_data, error = await _generate_quiz_question(
            QUIZ_STATE["topic"],
            QUIZ_STATE["difficulty"],
            QUIZ_STATE["question_number"],
        )
        if error:
            return {"content":[{"type":"text","text":error}],"isError":True}

        QUIZ_STATE["current_question"] = question_data["question"]
        QUIZ_STATE["current_answer"] = question_data["answer"]
        QUIZ_STATE["current_explanation"] = question_data["explanation"]

        formatted = (
            f"Question {QUIZ_STATE['question_number']}/{QUIZ_STATE['total']}:\n"
            f"{question_data['question']}\n"
            + "\n".join(f"{chr(65+i)}. {opt}" for i, opt in enumerate(question_data["options"]))
        )
        return {"content":[{"type":"text","text":formatted}]}

    if action in {"answer", "check"}:
        if not QUIZ_STATE.get("active") or not QUIZ_STATE.get("current_question"):
            return {"content":[{"type":"text","text":"No active quiz question. Start a quiz first."}],"isError":True}
        if not user_answer:
            return {"content":[{"type":"text","text":"Please provide your answer."}],"isError":True}

        correct = QUIZ_STATE["current_answer"].strip().lower()
        given = user_answer.strip().lower()
        letter_match = re.fullmatch(r"[a-d]", given)
        if letter_match:
            idx = ord(given) - ord("a")
            # The generated answer is option text; accept letter only by mapping it if possible.
            # Recreate option index from the current answer when possible.
            is_correct = False
            # We do not store options separately; exact text is therefore the reliable path.
            # A single-letter answer is treated as unsupported rather than guessed.
        else:
            is_correct = given == correct

        if is_correct:
            QUIZ_STATE["score"] += 1
            reply = f"Correct! {QUIZ_STATE['current_explanation']}"
        else:
            reply = f"Not quite. Correct answer: {QUIZ_STATE['current_answer']}. {QUIZ_STATE['current_explanation']}"
        QUIZ_STATE["question_number"] += 1
        if QUIZ_STATE["question_number"] > QUIZ_STATE["total"]:
            score = QUIZ_STATE["score"]
            total = QUIZ_STATE["total"]
            QUIZ_STATE["active"] = False
            reply += f"\nQuiz complete! Final score: {score}/{total}."
        return {"content":[{"type":"text","text":reply}]}

    return {"content":[{"type":"text","text":"Actions: start, answer, next, score, stop."}],"isError":True}


# ============================================================
# SMART EXPLAIN ENGINE
# ============================================================
async def smart_explain(arguments):
    request = str(arguments.get("request", "")).strip()
    mode = str(arguments.get("mode", "adaptive")).strip().lower() or "adaptive"
    language = str(arguments.get("language", "auto")).strip().lower() or "auto"
    audience = str(arguments.get("audience", "general")).strip().lower() or "general"
    depth = str(arguments.get("depth", "detailed")).strip().lower() or "detailed"
    format_type = str(arguments.get("format", "structured")).strip().lower() or "structured"
    examples = bool(arguments.get("examples", True))
    analogy = bool(arguments.get("analogy", True))
    recap = bool(arguments.get("recap", True))

    if not request:
        return {"content":[{"type":"text","text":"Please provide something to explain."}],"isError":True}

    mode_map = {
        "simple":"Explain in very simple language. Assume the listener is a beginner.",
        "beginner":"Explain for a complete beginner and define unfamiliar terms before using them.",
        "normal":"Explain at a normal practical level with enough technical detail to understand the idea.",
        "deep":"Give a deep technical explanation, including mechanisms, trade-offs, and important edge cases.",
        "expert":"Explain at an expert level and prioritize precise technical reasoning over simplification.",
        "one-minute":"Give a compact explanation that can be spoken naturally in about one minute.",
        "quick":"Give the essential explanation in a concise form without unnecessary detail.",
        "adaptive":"Choose the explanation depth automatically from the topic and the user's wording.",
    }
    depth_map = {
        "brief":"Keep it brief: the core idea plus only the most important supporting point.",
        "moderate":"Give a balanced explanation with the core idea, mechanism, and one useful example.",
        "detailed":"Give a detailed explanation with clear structure, mechanism, examples, and practical implications.",
        "deep":"Go deeply into how and why it works, important assumptions, trade-offs, and edge cases.",
    }
    format_map = {
        "structured":"Use a clear structure with short sections and compact bullets when helpful.",
        "conversation":"Answer naturally like a knowledgeable human speaking to the user.",
        "steps":"Explain as a logical step-by-step sequence.",
        "compare":"Organize the explanation around differences, similarities, trade-offs, and when to use each option.",
        "example-first":"Start with a concrete example, then explain the underlying concept.",
    }
    language_instruction = {
        "auto":"Use the language style of the user's request. For Hinglish, naturally mix Hindi and English.",
        "english":"Answer in clear natural English.",
        "hindi":"Answer in natural Hindi while keeping common technical terms in English when clearer.",
        "hinglish":"Answer in natural Hinglish, mixing Hindi and English naturally and conversationally.",
    }.get(language, "Use the language style of the user's request.")
    audience_instruction = {
        "child":"Explain so a curious child can understand it without assuming prior knowledge.",
        "beginner":"Assume no prior knowledge and define necessary terms.",
        "student":"Explain like a good teacher preparing a student to understand and recall the topic.",
        "general":"Assume a general intelligent listener with no guaranteed specialist background.",
        "developer":"Assume the listener is comfortable with programming and technical concepts.",
        "expert":"Assume strong domain knowledge and focus on precision and nuance.",
    }.get(audience, "Assume a general intelligent listener.")

    extras=[]
    if examples:
        extras.append("Include at least one concrete relevant example when it genuinely improves understanding.")
    if analogy:
        extras.append("Use one clear analogy only when it genuinely helps; do not force an analogy.")
    if recap:
        extras.append("End with a compact takeaway in one or two sentences.")

    instruction = f"""You are Megatron's Smart Explain Engine. Explain accurately, clearly, and naturally.
Do not invent facts, examples, sources, statistics, or capabilities. Stay focused and do not ask unnecessary follow-up questions.
{mode_map.get(mode, mode_map['adaptive'])}
{depth_map.get(depth, depth_map['detailed'])}
{format_map.get(format_type, format_map['structured'])}
{language_instruction}
{audience_instruction}
{' '.join(extras)}
When technical, explain what, why, and how where useful. When comparing, make comparison criteria explicit.
Do not begin with filler such as 'Sure' or 'Of course'."""

    prompt = instruction.strip() + "\n\nUSER EXPLANATION REQUEST:\n" + request
    return await ask_real_megatron(prompt)

# MCP TOOLS
# ============================================================
TOOLS = [
    {
        "name": "ask_megatron",
        "description": (
            "PRIMARY Megatron AI tool for normal questions, conversation, "
            "general knowledge, coding, ESP32, projects, reasoning, and "
            "problem solving. DO NOT use this tool for direct PC health/status "
            "requests such as 'check my PC', 'check my computer', 'PC health', "
            "'system status', CPU usage, RAM usage, GPU usage, disk space, "
            "battery status, uptime, or internet status. Use pc_health instead. "
            "Pass ONLY the user's original request."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The user's original question or request."
                    ),
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_status",
        "description": (
            "Check whether the Megatron MCP server is online."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_time",
        "description": (
            "Get the current local computer date and time."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "calculate",
        "description": (
            "Perform a mathematical calculation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                }
            },
            "required": ["expression"],
        },
    },
    {
        "name": "math_brain",
        "description": (
            "SMART MATH TOOL. ALWAYS use for real-world math requests such as "
            "percentages, percentage change, unit conversions, feet/inches to cm, "
            "km/miles, kg/lb, meters/feet, liters/gallons, Celsius/Fahrenheit, "
            "time conversions, lakh/million/crore conversions, square roots, "
            "powers, and arithmetic word problems. Do NOT use ask_megatron for "
            "direct calculation requests."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "The user's original math request."
                }
            },
            "required": ["request"],
        },
    },
    {
        "name": "converter",
        "description": (
            "SMART CONVERTER TOOL. Use for direct unit and currency conversion requests "
            "such as km to miles, kg to pounds, feet/inches to cm, Celsius to Fahrenheit, "
            "liters to gallons, GB to MB, time conversions, or INR/USD/EUR and other currency "
            "conversions. Do NOT use ask_megatron for direct conversion requests."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "The user's original conversion request."
                }
            },
            "required": ["request"],
        },
    },
    {
        "name": "smart_explain",
        "description": (
            "ADVANCED EXPLANATION TOOL. Use for requests asking Megatron to explain, "
            "teach, break down, clarify, simplify, compare, or deeply explain a topic. "
            "Supports adaptive, simple, beginner, normal, deep, expert, quick, and one-minute modes; "
            "structured, conversation, steps, compare, and example-first formats; English, Hindi, and Hinglish; "
            "audience levels from child to expert; examples, analogy, and recap controls. "
            "Do NOT use ask_megatron for direct explanation requests when this tool is available."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {"type":"string", "description":"The user's original explanation request."},
                "mode": {"type":"string", "description":"adaptive, simple, beginner, normal, deep, expert, quick, one-minute."},
                "depth": {"type":"string", "description":"brief, moderate, detailed, deep."},
                "format": {"type":"string", "description":"structured, conversation, steps, compare, example-first."},
                "language": {"type":"string", "description":"auto, english, hindi, hinglish."},
                "audience": {"type":"string", "description":"child, beginner, student, general, developer, expert."},
                "examples": {"type":"boolean"},
                "analogy": {"type":"boolean"},
                "recap": {"type":"boolean"},
            },
            "required":["request"],
        },
    },
    {
        "name": "smart_intent",
        "description": (
            "CENTRAL INTENT ROUTER. Classifies a request into the best Megatron capability "
            "before execution, such as math, converter, explanation, news, reminder, app control, "
            "file search, PC health/control, or normal AI conversation. Use when you want to inspect "
            "how Megatron would route a request."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request": {"type":"string", "description":"The original user request."},
            },
            "required": ["request"],
        },
    },
    {
        "name": "smart_quiz",
        "description": (
            "SMART QUIZ MODE. Start interactive quizzes on any topic. Supports easy, medium, hard, "
            "and expert difficulty and 1-10 questions. Actions: start, answer, next, score, stop."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type":"string", "description":"start, answer, next, score, or stop."},
                "topic": {"type":"string", "description":"Quiz topic."},
                "difficulty": {"type":"string", "description":"easy, medium, hard, expert."},
                "count": {"type":"integer", "description":"Number of questions from 1 to 10."},
                "answer": {"type":"string", "description":"Answer to the current question."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "soundboard",
        "description": (
            "VOICE EFFECTS / SOUNDBOARD. Play built-in Megatron sound effects such as "
            "startup, wake, diagnostic, stealth, alert, success, error, quiz_correct, "
            "quiz_wrong, and reminder. Use for commands like 'startup sequence', "
            "'system diagnostic', 'stealth mode', or 'alert mode'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Sound effect name or command."}
            },
            "required": ["action"],
        },
    },
    {
        "name": "smart_reminder",
        "description": (
            "SMART REMINDER TOOL. Use for 'remind me in 20 minutes to check the ESP32', "
            "'remind me at 7 PM to study', 'what reminders do I have', or 'cancel my reminder'. "
            "Use action add, list, or cancel. Add uses text plus delay_minutes, delay_seconds, or remind_at."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type":"string"}, "text": {"type":"string"},
                "delay_minutes": {"type":"number"}, "delay_seconds": {"type":"number"},
                "remind_at": {"type":"string"}, "id": {"type":"integer"}, "query": {"type":"string"}
            },
            "required": ["action"],
        },
    },
    {
        "name": "search_web",
        "description": (
            "SPECIALIZED LIVE WEB SEARCH TOOL. Use for latest news, "
            "current events, today's information, weather, prices, "
            "technology news, recent updates, and other time-sensitive "
            "information. Pass only the user's original search request."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "news_briefing",
        "description": (
            "LIVE NEWS BRIEFING TOOL. Use for requests like 'what's happening "
            "today', 'give me today's tech news', 'latest gaming news', "
            "'latest movie news', 'latest anime news', 'what's happening in India', "
            "or 'today's sports news'. Categories: general, india, tech, gaming, "
            "movies, anime, sports. Search current sources and return a concise, "
            "voice-friendly briefing. Do NOT use ask_megatron for live news requests."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "general, india, tech, gaming, movies, anime, or sports."
                },
                "query": {
                    "type": "string",
                    "description": "Optional original news request."
                }
            },
            "required": ["category"],
        },
    },
    {
        "name": "pc_health",
        "description": (
            "SMART PC HEALTH TOOL. ALWAYS use this tool for requests about the "
            "current Windows PC/computer health or status, including exact phrases "
            "like 'check my PC', 'check my computer', 'PC health', 'system status', "
            "'how is my PC', 'how is my computer', 'check system', 'check my system', "
            "'check CPU', 'CPU usage', 'RAM usage', 'memory usage', 'GPU usage', "
            "'disk space', 'storage', 'battery status', 'internet status', "
            "'uptime', or 'is my PC okay'. Returns CPU usage, RAM usage, C: disk space, "
            "uptime, internet connectivity, battery status, and GPU name in one compact result. "
            "Do NOT route these requests to ask_megatron or get_system_info."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "pc_control",
        "description": (
            "DIRECT PC CONTROL TOOL. Use for requests such as take a screenshot, "
            "open VS Code, open Downloads, minimize this window, maximize this window, "
            "close this window, switch window, press Ctrl C, press Enter, left click, "
            "right click, double click, move the mouse, or tell me the active window. "
            "Do NOT use ask_megatron for direct computer actions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "target": {"type": "string"},
                "keys": {
                    "type": ["array", "string"],
                    "items": {"type": "string"},
                },
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "smart_file_finder",
        "description": (
            "SMART FILE FINDER TOOL. Use this for requests such as "
            "'find my test.py', 'where is my screenshot', 'find all MP3 files', "
            "'find the latest screenshot', or 'open my latest screenshot'. "
            "Searches common local folders including Desktop, Downloads, "
            "Documents, and the Megatron project. Use action='find' to search "
            "or action='open' to open the best matching file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Filename, partial name, or keyword to search for.",
                },
                "file_type": {
                    "type": "string",
                    "description": (
                        "Optional type: any, image, audio, music, video, "
                        "document, or code."
                    ),
                },
                "action": {
                    "type": "string",
                    "description": "find or open.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "app_control",
        "description": (
            "SMART APP LAUNCHER. Use for direct app open/close requests such as "
            "'open Chrome', 'launch VS Code', 'open Calculator', 'open Notepad', "
            "'close Chrome', 'quit VS Code', or 'close Calculator'. "
            "Supported apps include Microsoft Edge, Google Chrome, File Explorer, "
            "Visual Studio Code, Arduino IDE, Notepad, and Calculator. "
            "Do NOT use ask_megatron for direct app-control requests."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "open or close.",
                },
                "target": {
                    "type": "string",
                    "description": "App name, e.g. Chrome, VS Code, Calculator, Notepad.",
                },
            },
            "required": ["action", "target"],
        },
    },
    {
        "name": "get_system_info",
        "description": (
            "Get accurate Windows PC hardware and software information "
            "including manufacturer, model, CPU, GPU, RAM, Windows edition, "
            "architecture, and Python version."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "open_app",
        "description": (
            "Open an approved Windows application: Microsoft Edge, "
            "File Explorer, Visual Studio Code, or Arduino IDE."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "app": {
                    "type": "string",
                    "description": (
                        "Microsoft Edge, File Explorer, VS Code, "
                        "or Arduino IDE."
                    ),
                }
            },
            "required": ["app"],
        },
    },
    {
        "name": "volume_up",
        "description": (
            "Increase Windows master volume."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "volume_down",
        "description": (
            "Decrease Windows master volume."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "set_volume",
        "description": (
            "Set Windows master volume to a percentage from 0 to 100."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "percent": {
                    "type": "number",
                    "description": (
                        "Volume percentage from 0 to 100."
                    ),
                }
            },
            "required": ["percent"],
        },
    },
    {
        "name": "mute",
        "description": (
            "Mute or unmute Windows master audio."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "play_pause",
        "description": (
            "Play or pause the current media player using "
            "the Windows media key."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "next_track",
        "description": (
            "Skip to the next media track."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "previous_track",
        "description": (
            "Go to the previous media track."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_battery_status",
        "description": (
            "Get the Windows battery charge and charging status."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "lock_pc",
        "description": (
            "Lock the current Windows session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

# ============================================================
# REAL MEGATRON BACKEND
# ============================================================
async def ask_real_megatron(query):
    query = str(query).strip()

    if not query:
        return {
            "content": [{
                "type": "text",
                "text": "Please provide a question.",
            }],
            "isError": True,
        }

    try:
        async with httpx.AsyncClient(
            timeout=90.0
        ) as client:

            response = await client.post(
                MEGATRON_API_URL,
                json={
                    "message": query,
                },
            )

            response.raise_for_status()

            data = response.json()

        if not data.get("success"):
            return {
                "content": [{
                    "type": "text",
                    "text": str(
                        data.get(
                            "error",
                            "Megatron API error.",
                        )
                    ),
                }],
                "isError": True,
            }

        reply = (
            data.get("reply", "")
            or "Megatron did not return a response."
        )

        print(
            "\nMegatron response:\n",
            reply,
        )

        return {
            "content": [{
                "type": "text",
                "text": str(reply),
            }]
        }

    except Exception as error:
        return {
            "content": [{
                "type": "text",
                "text": (
                    "Megatron backend is unavailable: "
                    f"{error}"
                ),
            }],
            "isError": True,
        }

# ============================================================
# WEB SEARCH
# ============================================================
async def search_real_web(query):
    query = str(query).strip()

    if not query:
        return {
            "content": [{
                "type": "text",
                "text": "Please provide a search query.",
            }],
            "isError": True,
        }

    if web_search is None:
        return {
            "content": [{
                "type": "text",
                "text": (
                    "Web search module is unavailable."
                ),
            }],
            "isError": True,
        }

    try:
        result = web_search(query)

        print(
            "\nWeb search result:\n",
            result,
        )

        return {
            "content": [{
                "type": "text",
                "text": str(result),
            }]
        }

    except Exception as error:
        return {
            "content": [{
                "type": "text",
                "text": (
                    "Web search failed: "
                    f"{error}"
                ),
            }],
            "isError": True,
        }

# ============================================================
# LIVE NEWS BRIEFING
# ============================================================
NEWS_QUERY_MAP = {
    "general": "latest important world and India news today",
    "india": "latest important India news today",
    "tech": "latest technology news today AI gadgets software cybersecurity",
    "gaming": "latest gaming news today games consoles PC gaming releases",
    "movies": "latest movie news today Hollywood Bollywood films trailers releases",
    "anime": "latest anime news today anime movies series manga announcements",
    "sports": "latest sports news today cricket football international sports",
}


async def news_briefing(arguments):
    category = " ".join(
        str(arguments.get("category", "general")).lower().split()
    )
    category = category if category in NEWS_QUERY_MAP else "general"
    original_query = str(arguments.get("query", "")).strip()

    base_query = NEWS_QUERY_MAP[category]
    if original_query:
        search_query = f"{original_query} latest today recent news"
    else:
        search_query = f"{base_query} recent last 24 hours"

    if web_search is None:
        return {
            "content": [{
                "type": "text",
                "text": "Live news search is unavailable because web search is not configured.",
            }],
            "isError": True,
        }

    try:
        raw = web_search(search_query)
        raw_text = str(raw)

        # Keep the summarizer prompt bounded even when a search provider returns
        # a very large page dump.
        source_excerpt = raw_text[:14000]

        summary_prompt = (
            "Create a concise voice briefing from the live news search results below. "
            "Use only information present in the source. Give 3 to 5 major items. "
            "Mention the topic and key fact in 1 sentence each. Avoid sensationalism, "
            "duplicates, filler, URLs, markdown tables, and unsupported claims. "
            "Start directly with the briefing. Do not say you searched the web.\n\n"
            f"CATEGORY: {category}\n"
            f"LIVE SEARCH RESULTS:\n{source_excerpt}"
        )

        summarized = await ask_real_megatron(
            summary_prompt
        )

        if summarized.get("isError"):
            return {
                "content": [{
                    "type": "text",
                    "text": source_excerpt[:7000],
                }],
                "isError": False,
            }

        text = summarized["content"][0]["text"]
        return {
            "content": [{
                "type": "text",
                "text": text,
            }]
        }

    except Exception as error:
        return {
            "content": [{
                "type": "text",
                "text": f"News briefing failed: {error}",
            }],
            "isError": True,
        }

# ============================================================
# LOCAL INTENT ROUTING
# ============================================================
def _normalized_text(value):
    return " ".join(str(value or "").strip().lower().split())

def classify_smart_intent(request):
    q = _normalized_text(request)
    if not q:
        return {"intent":"unknown", "tool":"ask_megatron", "reason":"empty request"}

    # Strongest/safest direct-action intents first.
    if re.search(r"\b(check|how is|status|health|cpu|ram|gpu|storage|disk|battery|internet|uptime)\b.*\b(pc|computer|system)\b|\bcheck (my )?pc\b", q):
        return {"intent":"pc_health", "tool":"pc_health", "reason":"PC health/status request"}
    if any(x in q for x in ("take a screenshot", "screenshot", "press ctrl", "press enter", "left click", "right click", "double click", "active window")):
        return {"intent":"pc_control", "tool":"pc_control", "reason":"direct PC action"}
    if any(x in q for x in ("find my ", "where is my ", "locate my ", "open my latest", "find the latest screenshot")) or any(q.endswith(ext) for ext in (".py", ".js", ".txt", ".pdf", ".png", ".jpg", ".mp3", ".zip")):
        return {"intent":"file_search", "tool":"smart_file_finder", "reason":"file/folder request"}

    if any(x in q for x in ("open ", "launch ", "start ", "close ", "quit ")) and any(app in q for app in ("chrome", "brave", "edge", "vs code", "vscode", "arduino", "notepad", "calculator", "file explorer", "vlc", "steam")):
        return {"intent":"app_control", "tool":"app_control", "reason":"application command"}

    if any(x in q for x in ("remind me", "reminder", "remind me in", "remind me at", "what reminders")):
        return {"intent":"reminder", "tool":"smart_reminder", "reason":"reminder request"}

    if any(x in q for x in ("what's happening", "whats happening", "latest news", "today's news", "latest tech news", "latest gaming news", "latest movie news", "latest anime news", "latest sports news", "news from india", "what's new in gaming", "what's new in tech")):
        return {"intent":"news", "tool":"news_briefing", "reason":"live news request"}

    # Strict conversion detection: number + unit + explicit direction.
    conversion_re = re.compile(
        r"(?P<num>-?\d+(?:\.\d+)?)\s*(?P<unit>km|kilometer(?:s)?|mi|miles|kg|kilogram(?:s)?|lb|lbs|pound(?:s)?|cm|centimeter(?:s)?|m|meter(?:s)?|ft|feet|foot|in|inch(?:es)?|c|celsius|f|fahrenheit|gb|mb|tb|gallon(?:s)?|liter(?:s)?|litre(?:s)?|hour(?:s)?|minute(?:s)?|second(?:s)?|rupees?|dollars?|usd|inr|eur|euros?)\s*(?:to|into|in|me|mein|में|को|में|=)\s*(?P<to>[a-z₹$€£]+)",
        re.IGNORECASE,
    )
    if conversion_re.search(q) or any(x in q for x in ("convert ", "convert karo", "convert kar", "में convert", "में बदलो", "ko miles", "ko pounds")) and re.search(r"\d", q):
        return {"intent":"conversion", "tool":"converter", "reason":"numeric unit/currency conversion"}

    explain = ("explain", "teach me", "break down", "clarify", "simplify", "deep dive", "step by step", "difference between", "compare", "samjhao", "detail mein")
    if any(x in q for x in explain):
        return {"intent":"explanation", "tool":"smart_explain", "reason":"explanation/teaching request"}

    if any(x in q for x in ("quiz me", "start a quiz", "test me on", "ask me questions about", "quiz mode")):
        return {"intent":"quiz", "tool":"smart_quiz", "reason":"interactive quiz request"}

    math_re = re.compile(r"\b(?:what is|calculate|solve|how much is)\b.*\d|[0-9]+\s*[+\-*/^]\s*[0-9]+")
    if math_re.search(q) or any(x in q for x in ("percentage of", "percent of", "square root", "to the power of", "times", "divided by")):
        return {"intent":"math", "tool":"math_brain", "reason":"calculation request"}

    return {"intent":"conversation", "tool":"ask_megatron", "reason":"general conversation/question"}


def route_local_intent(tool_name, arguments):
    name = str(tool_name or "").strip().lower()
    args = dict(arguments or {})
    query = _normalized_text(args.get("query", ""))

    if name == "smart_intent":
        result = classify_smart_intent(args.get("request", ""))
        return "smart_intent", {"request": args.get("request", ""), "result": result}, True

    if name == "math_brain":
        classification = classify_smart_intent(args.get("request", args.get("query", "")))
        if classification["tool"] == "converter":
            math_query = _normalized_text(args.get("request", args.get("query", "")))
            print("\n[ROUTER] math_brain -> converter")
            return "converter", {"request": math_query}, True
        return name, args, False

    if name != "ask_megatron":
        return name, args, False

    classification = classify_smart_intent(query)
    tool = classification["tool"]
    print(f"\n[SMART INTENT] {classification['intent']} -> {tool}")

    if tool == "smart_explain":
        mode="adaptive"; depth="detailed"; language="auto"; fmt="structured"; audience="general"
        if "like i'm 10" in query or "like im 10" in query or "for a child" in query:
            mode="simple"; audience="child"
        elif "beginner" in query or "simple words" in query:
            mode="beginner"; audience="beginner"
        elif "deep" in query or "deep dive" in query:
            mode="deep"; depth="deep"
        elif "expert" in query:
            mode="expert"; audience="expert"
        if "one minute" in query or "1 minute" in query:
            mode="one-minute"; depth="brief"
        if "step by step" in query:
            fmt="steps"
        elif "compare" in query or "difference between" in query:
            fmt="compare"
        elif "example first" in query:
            fmt="example-first"
        if "hinglish" in query:
            language="hinglish"
        elif "in hindi" in query or "hindi mein" in query:
            language="hindi"
        elif "in english" in query or "english mein" in query:
            language="english"
        return "smart_explain", {"request":query,"mode":mode,"depth":depth,"format":fmt,"language":language,"audience":audience,"examples":True,"analogy":True,"recap":True}, True

    if tool == "converter":
        return "converter", {"request": query}, True
    if tool == "news_briefing":
        category = "general"
        if any(k in query for k in ("india", "indian")): category="india"
        elif any(k in query for k in ("tech", "technology", "ai", "gadgets", "cybersecurity")): category="tech"
        elif any(k in query for k in ("gaming", "game", "games", "playstation", "xbox", "steam")): category="gaming"
        elif any(k in query for k in ("movie", "movies", "film", "hollywood", "bollywood")): category="movies"
        elif any(k in query for k in ("anime", "manga")): category="anime"
        elif any(k in query for k in ("sports", "cricket", "football")): category="sports"
        return "news_briefing", {"category":category, "query":query}, True
    if tool == "smart_reminder":
        return "smart_reminder", {"action":"add", "text":query}, True
    # Soundboard / voice-effect commands should never go to the LLM.
    sound_commands = {
        "startup sequence": "startup",
        "system diagnostic": "diagnostic",
        "activate stealth mode": "stealth",
        "stealth mode": "stealth",
        "alert mode": "alert",
        "sound test": "tool_start",
    }
    if query in sound_commands:
        effect = sound_commands[query]
        print(f"\n[SMART INTENT] soundboard -> {effect}")
        return "soundboard", {"action": effect}, True

    if tool == "smart_quiz":
        return "smart_quiz", {"action":"start", "topic":query, "difficulty":"medium", "count":5}, True
    if tool == "pc_health": return "pc_health", {}, True
    if tool == "pc_control": return "pc_control", {"action":"unknown"}, True
    if tool == "smart_file_finder": return "smart_file_finder", {"query":query, "action":"find"}, True
    if tool == "app_control":
        action = "close" if query.startswith(("close ", "quit ")) else "open"
        return "app_control", {"action":action, "target":query}, True
    return name, args, False


# ============================================================
# TOOL EXECUTION
# ============================================================
async def _execute_tool(name, arguments):
    if name == "ask_megatron":
        return await ask_real_megatron(
            arguments.get(
                "query",
                "",
            )
        )

    if name == "get_status":
        return {
            "content": [{
                "type": "text",
                "text": (
                    "Megatron MCP server is online "
                    "and operational."
                ),
            }]
        }

    if name == "get_time":
        now = datetime.datetime.now()

        return {
            "content": [{
                "type": "text",
                "text": now.strftime("%I:%M %p IST | %d %B %Y"),
            }]
        }

    if name == "calculate":
        expression = arguments.get(
            "expression",
            "",
        )

        try:
            result = safe_calculate(
                expression
            )

            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"{expression} = {result}"
                    ),
                }]
            }

        except Exception as error:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Calculation error: {error}"
                    ),
                }],
                "isError": True,
            }

    if name == "math_brain":
        request = arguments.get("request", "")
        handled, result = math_brain(request)
        return {
            "content": [{
                "type": "text",
                "text": result,
            }],
            "isError": not handled,
        }

    if name == "converter":
        request = arguments.get("request", "")
        handled, result = smart_converter(request)
        if result.startswith("CURRENCY_LIVE_SEARCH|"):
            search_request = result.split("|", 1)[1]
            return await search_real_web(
                f"latest exchange rate conversion: {search_request}. Return a concise current conversion."
            )
        return {
            "content": [{"type": "text", "text": result}],
            "isError": not handled,
        }

    if name == "smart_intent":
        result = classify_smart_intent(arguments.get("request", ""))
        return {"content":[{"type":"text","text":json.dumps(result, ensure_ascii=False)}]}

    if name == "soundboard":
        return soundboard_tool(arguments)

    if name == "smart_quiz":
        return await smart_quiz(arguments)

    if name == "smart_explain":
        return await smart_explain(arguments)

    if name == "smart_reminder":
        return execute_reminder(arguments)

    if name == "news_briefing":
        return await news_briefing(arguments)

    if name == "search_web":
        return await search_real_web(
            arguments.get(
                "query",
                "",
            )
        )



    if name == "pc_health":
        health = get_pc_health()
        return {
            "content": [{
                "type": "text",
                "text": json.dumps(
                    health,
                    ensure_ascii=False,
                ),
            }]
        }

    if name == "pc_control":
        return execute_pc_control(arguments)

    if name == "smart_file_finder":
        return execute_file_finder(arguments)

    if name == "app_control":
        return execute_app_control(arguments)

    if name == "get_system_info":
        return {
            "content": [{
                "type": "text",
                "text": json.dumps(
                    get_windows_system_info(),
                    ensure_ascii=False,
                    indent=2,
                ),
            }]
        }

    if name == "open_app":
        success, message = open_allowed_app(
            arguments.get(
                "app",
                "",
            )
        )

        return {
            "content": [{
                "type": "text",
                "text": message,
            }],
            "isError": not success,
        }

    if name == "volume_up":
        press_media_key(
            VK_VOLUME_UP
        )

        return {
            "content": [{
                "type": "text",
                "text": "Volume increased.",
            }]
        }

    if name == "volume_down":
        press_media_key(
            VK_VOLUME_DOWN
        )

        return {
            "content": [{
                "type": "text",
                "text": "Volume decreased.",
            }]
        }

    if name == "set_volume":
        percent = arguments.get(
            "percent",
            0,
        )

        try:
            percent = max(
                0.0,
                min(
                    100.0,
                    float(percent),
                ),
            )

        except (ValueError, TypeError):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "Volume must be a number "
                        "from 0 to 100."
                    ),
                }],
                "isError": True,
            }

        success, message = set_volume_percent(
            percent
        )

        return {
            "content": [{
                "type": "text",
                "text": message,
            }],
            "isError": not success,
        }

    if name == "mute":
        press_media_key(
            VK_VOLUME_MUTE
        )

        return {
            "content": [{
                "type": "text",
                "text": "Mute toggled.",
            }]
        }

    if name == "play_pause":
        press_media_key(
            VK_MEDIA_PLAY_PAUSE
        )

        return {
            "content": [{
                "type": "text",
                "text": "Play/pause toggled.",
            }]
        }

    if name == "next_track":
        press_media_key(
            VK_MEDIA_NEXT_TRACK
        )

        return {
            "content": [{
                "type": "text",
                "text": "Next track.",
            }]
        }

    if name == "previous_track":
        press_media_key(
            VK_MEDIA_PREV_TRACK
        )

        return {
            "content": [{
                "type": "text",
                "text": "Previous track.",
            }]
        }

    if name == "get_battery_status":
        return {
            "content": [{
                "type": "text",
                "text": json.dumps(
                    get_battery_status(),
                    ensure_ascii=False,
                    indent=2,
                ),
            }]
        }

    if name == "lock_pc":
        try:
            ctypes.windll.user32.LockWorkStation()

            return {
                "content": [{
                    "type": "text",
                    "text": "PC locked.",
                }]
            }

        except Exception as error:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "Could not lock the PC: "
                        f"{error}"
                    ),
                }],
                "isError": True,
            }

    return {
        "content": [{
            "type": "text",
            "text": f"Unknown tool: {name}",
        }],
        "isError": True,
    }

# ============================================================
# SOUNDBOARD-AWARE TOOL EXECUTION WRAPPER
# ============================================================
async def execute_tool(name, arguments):
    substantive = {
        "ask_megatron", "math_brain", "converter", "smart_explain",
        "smart_intent", "smart_quiz", "smart_reminder", "search_web",
        "news_briefing", "pc_health", "pc_control", "smart_file_finder",
        "app_control", "get_system_info", "open_app", "volume_up",
        "volume_down", "set_volume", "mute", "play_pause", "next_track",
        "previous_track", "get_battery_status", "lock_pc", "soundboard"
    }
    if name in substantive and name != "soundboard":
        play_effect("tool_start")
    try:
        result = await _execute_tool(name, arguments)
        if name in substantive and name != "soundboard":
            if result.get("isError"):
                play_effect("error")
            else:
                play_effect("success")
        return result
    except Exception:
        if name in substantive and name != "soundboard":
            play_effect("error")
        raise

# ============================================================
# MAIN MCP SERVER
# ============================================================
async def main():
    play_effect("startup")
    asyncio.create_task(reminder_worker())
    print("=" * 60)
    print(
        "MEGATRON MCP SERVER"
    )
    print("=" * 60)

    print(
        "\nAvailable tools:"
    )

    for tool in TOOLS:
        print(
            " -",
            tool["name"],
        )

    print(
        "\nMegatron API:",
        MEGATRON_API_URL,
    )
    print(
        "\nConnecting to Xiaozhi..."
    )

    try:
        async with websockets.connect(
            XIAOZHI_MCP_URL,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:

            print(
                "WebSocket connected!"
            )

            while True:
                try:
                    raw = await ws.recv()

                except websockets.ConnectionClosed as error:
                    print(
                        "\nWebSocket closed"
                    )

                    print(
                        "Code:",
                        error.code,
                    )

                    print(
                        "Reason:",
                        error.reason,
                    )

                    break

                print(
                    "\nXIAOZHI:"
                )
                print(raw)

                try:
                    message = json.loads(
                        raw
                    )

                except json.JSONDecodeError:
                    print(
                        "Non-JSON message ignored."
                    )
                    continue

                method = message.get(
                    "method"
                )

                request_id = message.get(
                    "id"
                )

                params = message.get(
                    "params",
                    {},
                )

                if method == "initialize":
                    await send_json(
                        ws,
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {
                                "protocolVersion": "2024-11-05",
                                "capabilities": {
                                    "tools": {}
                                },
                                "serverInfo": {
                                    "name": "Megatron",
                                    "version": "1.0.0",
                                },
                            },
                        },
                    )

                    print(
                        "Initialize response sent."
                    )

                elif method == "notifications/initialized":
                    print(
                        "Xiaozhi initialized."
                    )

                elif method == "tools/list":
                    await send_json(
                        ws,
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {
                                "tools": TOOLS,
                            },
                        },
                    )

                    print(
                        len(TOOLS),
                        "tools advertised.",
                    )

                elif method == "ping":
                    await send_json(
                        ws,
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {},
                        },
                    )

                    print(
                        "Ping response sent."
                    )

                elif method == "tools/call":
                    original_tool_name = params.get("name")

                    arguments = params.get(
                        "arguments",
                        {},
                    )

                    tool_name, arguments, rerouted = route_local_intent(
                        original_tool_name,
                        arguments,
                    )

                    if rerouted:
                        print("[ROUTER] Original tool:", original_tool_name)
                        print("[ROUTER] Routed tool:", tool_name)

                    print(
                        "\nTOOL CALL:"
                    )

                    print(
                        "Tool:",
                        tool_name,
                    )

                    print(
                        "Arguments:",
                        arguments,
                    )

                    try:
                        result = await execute_tool(
                            tool_name,
                            arguments,
                        )

                        response = {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": result,
                        }

                    except Exception as error:
                        print(
                            "Tool execution error:",
                            error,
                        )

                        response = {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {
                                "code": -32603,
                                "message": str(error),
                            },
                        }

                    await send_json(
                        ws,
                        response,
                    )

                    print(
                        "Tool response sent."
                    )

                elif method:
                    if request_id is not None:
                        await send_json(
                            ws,
                            {
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "error": {
                                    "code": -32601,
                                    "message": (
                                        "Method not found: "
                                        f"{method}"
                                    ),
                                },
                            },
                        )

    except KeyboardInterrupt:
        print(
            "\nMCP server stopped."
        )

    except websockets.ConnectionClosed as error:
        print(
            "\nWebSocket connection closed."
        )

        print(
            "Code:",
            error.code,
        )

        print(
            "Reason:",
            error.reason,
        )

    except Exception as error:
        print(
            "\nServer error:"
        )

        print(
            type(error).__name__
        )

        print(error)

if __name__ == "__main__":
    asyncio.run(main())

import os
import re
import time
from typing import Dict, List, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

from context_manager import (
    add_message,
    build_context,
    trim_messages,
)

from intent_manager import (
    normalize,
    is_memory_command,
    detect_memory_intent,
    extract_memory_content,
    extract_forget_keyword,
)

from memory import (
    init_memory,
    save_memory,
    get_memories,
    search_memories,
    delete_memory,
    update_memory,
    get_memory_context,
)

from tools.web_search import web_search


# ============================================================
# STT CLEANING
# ============================================================

def clean_stt_query(text: str) -> str:
    if not text:
        return text

    cleaned = text.strip()

    cleaned = re.sub(
        r'\b(गैबिटल|गेबिटल|केपिटल|कैपीटल|गैपीटल|कैपिटल|gabital|gebital|kepital)\b',
        'capital',
        cleaned,
        flags=re.IGNORECASE
    )

    cleaned = re.sub(
        r'(capital|कैपिटल)\s*(गया|गया hai|गया है)',
        r'\1 kya hai',
        cleaned,
        flags=re.IGNORECASE
    )

    cleaned = re.sub(
        r'\b(गया hai|गया है)\b$',
        'kya hai',
        cleaned,
        flags=re.IGNORECASE
    )

    replacements = {
        r'\b(farch|फरच|farc|फारच)\b': 'search',
        r'\b(infomation|इन्फॉर्मेशन|इंफॉर्मेशन)\b': 'information',
        r'\b(न्यूज़|न्यूज)\s*(फरच|farch)\b': 'news search',
    }

    for pattern, replacement in replacements.items():
        cleaned = re.sub(
            pattern,
            replacement,
            cleaned,
            flags=re.IGNORECASE
        )

    return cleaned


# ============================================================
# ENVIRONMENT / MODEL ROUTING
# ============================================================

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Fast model: high-volume voice/simple requests.
FAST_MODEL = os.getenv(
    "GEMINI_FAST_MODEL",
    "gemini-2.5-flash-lite"
)

# Smart/default model: harder reasoning and deeper explanations.
MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
)

MAX_TOKENS = int(
    os.getenv(
        "MEGATRON_MAX_TOKENS",
        "320"
    )
)

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY not found in .env"
    )

client = genai.Client(
    api_key=GEMINI_API_KEY
)

# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = """
You are Megatron, a natural, intelligent AI voice assistant.

Personality:
- Calm, friendly, practical, confident, respectful.
- Speak naturally in Indian Hindi/Hinglish.
- Match the user's language.
- Keep simple answers concise.
- Be detailed only when needed.
- Never sound robotic or scripted.

Conversation continuity:
- Treat the conversation as one continuous discussion.
- Resolve follow-up questions using recent context.
- Understand pronouns and references such as "it", "that", "this",
  "he", "she", "they", "there", "isko", "usko", "ye", and "woh"
  from the previous turns.
- When the user asks a short follow-up like "why?", "how?", "kitna?",
  "and then?", or "iska kya?", infer the missing subject from the
  most recent relevant conversation context.
- Do not ask the user to repeat context that is already available.
- Only ask for clarification when the context genuinely supports
  multiple plausible meanings.
- Never mention the internal context system.

Voice response:
- For simple questions, answer directly in 1-3 short sentences.
- Avoid unnecessary preambles and repeated greetings.
- Do not restate the question.

Accuracy:
- Do not invent facts.
- For current information, use web search when it is available.
- For simple factual questions, answer directly and stop.
- Do not add unsolicited follow-up questions, suggestions, or "by the way" comments.
- Do not invent context about what the user has seen, visited, or experienced.
- Be honest when uncertain.

Memory:
- Use relevant long-term memories naturally.
- Do not mention databases or internal memory systems.

Technical expertise:
- Python, C, C++, ESP32, ESP-IDF, Arduino, HTML, CSS, JavaScript,
  FastAPI, APIs, WebSocket, MCP, AI, IoT, robotics and embedded systems.
""".strip()


# ============================================================
# CONVERSATION
# ============================================================

conversation: List[Dict[str, str]] = [
    {
        "role": "system",
        "content": SYSTEM_PROMPT,
    }
]


# ============================================================
# INITIALIZE MEMORY
# ============================================================

init_memory()


# ============================================================
# SMART AUTO-MEMORY
# ============================================================

AUTO_MEMORY_PATTERNS = [
    ("preference", r"\bmy favorite (?:is )?(.+)"),
    ("preference", r"\bmy fav(?:orite)? (?:is )?(.+)"),
    ("preference", r"\bi (?:really )?like (.+)"),
    ("preference", r"\bi love (.+)"),
    ("preference", r"\bi (?:do not|don't|dont) like (.+)"),
    ("preference", r"\bi prefer (.+)"),
    ("profile", r"\bmy name is ([A-Za-z][A-Za-z0-9 _.-]{1,40})"),
    ("profile", r"\bcall me ([A-Za-z][A-Za-z0-9 _.-]{1,40})"),
    ("project", r"\bi(?:'m| am) working on (.+)"),
    ("project", r"\bi(?:'m| am) building (.+)"),
    ("project", r"\bmy project is (.+)"),
    ("tech", r"\bi use (.+)"),
    ("tech", r"\bi(?:'m| am) using (.+)"),
]


def detect_auto_memory(text: str):
    cleaned = " ".join(text.strip().split())

    if is_memory_command(cleaned) or detect_memory_intent(cleaned):
        return None

    for category, pattern in AUTO_MEMORY_PATTERNS:
        match = re.search(
            pattern,
            cleaned,
            re.IGNORECASE
        )

        if not match:
            continue

        value = (
            match.group(1)
            .strip()
            .rstrip(".!?")
        )

        if not value:
            continue

        if len(value) < 2 or len(value) > 180:
            continue

        blocked = {
            "nothing",
            "everything",
            "something",
            "this",
            "that",
            "it",
            "you",
            "the assistant",
        }

        if value.lower() in blocked:
            continue

        return category, value

    return None


def save_auto_memory(text: str):
    detected = detect_auto_memory(text)

    if not detected:
        return None

    category, value = detected

    if save_memory(
        text.strip(),
        category=category
    ):
        return category, value

    return None


# ============================================================
# LIVE WEB SEARCH
# ============================================================

LIVE_SEARCH_PATTERNS = (
    "latest",
    "current",
    "today",
    "today's",
    "todays",
    "right now",
    "live",
    "recent",
    "breaking news",
    "latest news",
    "current news",
    "price today",
    "latest price",
    "weather",
    "traffic",
    "stock price",
    "crypto price",
    "recent update",
)


def should_search_web(text: str) -> bool:
    normalized = normalize(text)

    return any(
        pattern in normalized
        for pattern in LIVE_SEARCH_PATTERNS
    )


# ============================================================
# MEMORY HELPERS
# ============================================================

def format_memory_rows(rows) -> str:
    if not rows:
        return "I don't have any matching memories."

    return "\n".join(
        f"- [{category}] {content}"
        for category, content in rows
    )


def handle_memory_command(text: str) -> Optional[str]:
    intent = detect_memory_intent(text)
    normalized = normalize(text)

    if intent == "remember":
        content = extract_memory_content(text)

        if not content:
            return "Mujhe kya remember karna hai, woh batao."

        if save_memory(
            content,
            category="general"
        ):
            return "Done bhai, main ye yaad rakhunga."

        return "Ye memory already saved hai."

    if intent == "show_memory":
        rows = get_memories(limit=20)

        if not rows:
            return "Abhi mere paas koi saved memory nahi hai."

        return (
            "Mujhe ye cheezein yaad hain:\n"
            + format_memory_rows(rows)
        )

    if intent == "forget":
        keyword = extract_forget_keyword(text)

        if not keyword:
            return "Kya bhoolna hai, woh batao."

        deleted = delete_memory(keyword)

        if deleted:
            return f"Done bhai, {deleted} memory delete kar di."

        return "Mujhe us keyword se koi matching memory nahi mili."

    if normalized == "/memories":
        rows = get_memories(limit=20)

        if not rows:
            return "No saved memories."

        return format_memory_rows(rows)

    if normalized.startswith("/searchmemory "):
        keyword = text.strip()[
            len("/searchmemory "):
        ].strip()

        results = search_memories(keyword)

        if results:
            return format_memory_rows(results)

        return f"No memories found for: {keyword}"

    if normalized.startswith("/forget "):
        keyword = text.strip()[
            len("/forget "):
        ].strip()

        deleted = delete_memory(keyword)
        return f"Deleted {deleted} memory(s)."

    if normalized.startswith("/update "):
        payload = text.strip()[
            len("/update "):
        ].strip()

        if "=>" not in payload:
            return "Format: /update old keyword => new content"

        old_keyword, new_content = (
            part.strip()
            for part in payload.split("=>", 1)
        )

        if update_memory(
            old_keyword,
            new_content
        ):
            return "Memory updated successfully."

        return "Matching memory nahi mili ya update possible nahi tha."

    return None


# ============================================================
# SHORT FOLLOW-UP DETECTION
# ============================================================

def is_short_follow_up(text: str) -> bool:
    normalized = normalize(text)

    followups = {
        "why",
        "why?",
        "how",
        "how?",
        "then?",
        "and then?",
        "what about it",
        "what about that",
        "kitna",
        "kitni",
        "kab",
        "kyun",
        "kaise",
        "iska kya",
        "iska kya?",
        "isko kaise",
        "uska kya",
        "fir kya",
        "phir kya",
        "aur?",
    }

    if normalized in followups:
        return True

    words = normalized.split()

    if len(words) <= 5:
        reference_terms = (
            "it",
            "that",
            "this",
            "they",
            "there",
            "isko",
            "usko",
            "ye",
            "woh",
            "iska",
            "uska",
        )

        return any(
            word in words
            for word in reference_terms
        )

    return False


# ============================================================
# CONTEXT ENRICHMENT
# ============================================================

def build_smart_user_message(user_message: str) -> str:
    if not is_short_follow_up(user_message):
        return user_message

    recent_user_messages = [
        item["content"]
        for item in conversation
        if item.get("role") == "user"
    ][-3:]

    if not recent_user_messages:
        return user_message

    previous_context = "\n".join(
        f"- {item}"
        for item in recent_user_messages
    )

    return (
        f"{user_message}\n\n"
        "Conversation continuity hint: resolve this "
        "follow-up using the most recent relevant "
        "context below. Do not mention this hint.\n"
        f"{previous_context}"
    )


# ============================================================
# COMPLEXITY / MODEL ROUTING
# ============================================================

def should_force_smart_model(text: str) -> bool:
    """
    Route genuinely harder requests to the stronger model.
    Local/tool commands should be handled before this function.
    """
    normalized = normalize(text)
    words = normalized.split()

    complex_markers = (
        "deep",
        "deep dive",
        "detailed explanation",
        "in detail",
        "step by step",
        "architecture",
        "debug",
        "debugging",
        "optimize",
        "optimization",
        "refactor",
        "compare",
        "difference between",
        "tradeoff",
        "trade-off",
        "why does",
        "how does",
        "how would i",
        "design",
        "implement",
        "build",
        "algorithm",
        "reasoning",
        "analyze",
        "analysis",
        "prove",
        "derive",
        "complex",
        "advanced",
        "expert",
        "explain",
        "teach me",
        "samjhao",
        "detail mein",
        "deeply",
        "why",
    )

    if any(marker in normalized for marker in complex_markers):
        return True

    # Long technical requests are better handled by the stronger model.
    if len(words) >= 32:
        return True

    # Code-heavy prompts tend to need better reasoning.
    code_markers = (
        "python",
        "javascript",
        "typescript",
        "c++",
        "c#",
        "esp32",
        "esp-idf",
        "fastapi",
        "websocket",
        "mcp",
        "asyncio",
        "error",
        "traceback",
        "exception",
        "stack trace",
    )

    technical_hits = sum(
        1 for marker in code_markers
        if marker in normalized
    )

    return technical_hits >= 2


# ============================================================
# GEMINI
# ============================================================

def ask_gemini(
    messages: List[Dict[str, str]],
    fast: bool = False,
    smart: bool = False,
) -> str:
    if smart:
        model = MODEL
        temperature = 0.55
        max_tokens = min(MAX_TOKENS, 500)
    elif fast:
        model = FAST_MODEL
        temperature = 0.40
        max_tokens = min(MAX_TOKENS, 180)
    else:
        model = MODEL
        temperature = 0.55
        max_tokens = MAX_TOKENS

    system_parts = []
    contents = []

    for message in messages:
        role = message.get("role", "user")
        content = str(message.get("content", "")).strip()

        if not content:
            continue

        if role == "system":
            system_parts.append(content)
            continue

        contents.append(
            types.Content(
                role="model" if role == "assistant" else "user",
                parts=[types.Part.from_text(text=content)]
            )
        )

    if not contents:
        raise RuntimeError("No conversation content available for Gemini.")

    print(f"[MODEL] {model}")

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction="\n\n".join(system_parts) if system_parts else None,
            temperature=temperature,
            max_output_tokens=max_tokens,
        ),
    )

    answer = (response.text or "").strip()

    if not answer:
        return "Sorry bhai, mujhe response generate nahi ho paya."

    return answer


# ============================================================
# FAST VOICE MODE
# ============================================================

def should_use_fast_response(
    text: str,
    web_context: str = ""
) -> bool:
    normalized = normalize(text)
    word_count = len(normalized.split())

    if web_context:
        return False

    long_markers = (
        "explain",
        "detail",
        "detailed",
        "step by step",
        "samjhao",
        "detail mein",
        "why does",
        "how does",
        "deep",
        "deep dive",
        "compare",
        "difference",
        "debug",
        "optimize",
        "architecture",
    )

    if any(
        marker in normalized
        for marker in long_markers
    ):
        return False

    return word_count <= 14


# ============================================================
# MAIN PROCESSOR
# ============================================================

def process_message(
    user_message: str
) -> str:
    started_total = time.perf_counter()
    global conversation

    if (
        not user_message
        or not user_message.strip()
    ):
        return "Haan bhai, bolo."

    user_message = clean_stt_query(
        user_message
    )
    user_message = user_message.strip()

    # --------------------------------------------------------
    # EXPLICIT MEMORY COMMANDS
    # --------------------------------------------------------

    if (
        is_memory_command(user_message)
        or detect_memory_intent(user_message)
    ):
        memory_reply = handle_memory_command(
            user_message
        )

        if memory_reply:
            add_message(
                conversation,
                "user",
                user_message
            )

            add_message(
                conversation,
                "assistant",
                memory_reply
            )

            trim_messages(
                conversation
            )

            print(
                f"[TIMING] total_process_message="
                f"{time.perf_counter() - started_total:.3f}s"
            )
            return memory_reply

    # --------------------------------------------------------
    # SMART AUTO-MEMORY
    # --------------------------------------------------------

    auto_memory = save_auto_memory(
        user_message
    )

    if auto_memory:
        category, value = auto_memory

        print(
            f"[AUTO MEMORY] Saved "
            f"({category}): {value}"
        )

    # --------------------------------------------------------
    # WEB SEARCH
    # --------------------------------------------------------

    web_context = ""

    if should_search_web(
        user_message
    ):
        print(
            f"\n[WEB SEARCH] "
            f"{user_message}"
        )

        web_started = time.perf_counter()

        web_context = web_search(
            user_message
        )

        print(
            f"[TIMING] tavily_search="
            f"{time.perf_counter() - web_started:.3f}s"
        )

    # --------------------------------------------------------
    # LONG-TERM MEMORY
    # --------------------------------------------------------

    memory_context = get_memory_context(
        limit=6
    )

    # --------------------------------------------------------
    # SMART CONTINUITY
    # --------------------------------------------------------

    enriched_message = (
        build_smart_user_message(
            user_message
        )
    )

    if web_context:
        enriched_message += (
            "\n\nLIVE WEB INFORMATION:\n"
            + web_context
            + "\n\nUse this information "
            "for current facts. "
            "Do not invent details that "
            "are not supported by it."
        )

    # --------------------------------------------------------
    # STORE USER MESSAGE
    # --------------------------------------------------------

    add_message(
        conversation,
        "user",
        enriched_message
    )

    # --------------------------------------------------------
    # SMART CONTEXT
    # --------------------------------------------------------

    messages_for_model = build_context(
        conversation,
        memory_context=memory_context,
        current_user_message=enriched_message,
    )

    # --------------------------------------------------------
    # MODEL SELECTION
    # --------------------------------------------------------

    fast_mode = should_use_fast_response(
        user_message,
        web_context
    )

    smart_mode = should_force_smart_model(
        user_message
    )

    # Smart model wins over fast mode.
    if smart_mode:
        fast_mode = False

    try:
        if smart_mode:
            print(
                "\n[MEGATRON] "
                "Smart reasoning mode..."
            )
        elif fast_mode:
            print(
                "\n[MEGATRON] "
                "Fast voice mode..."
            )
        else:
            print(
                "\n[MEGATRON] "
                "Normal mode..."
            )

        gemini_started = time.perf_counter()

        try:
            reply = ask_gemini(
                messages_for_model,
                fast=fast_mode,
                smart=smart_mode,
            )
        except Exception as primary_error:
            # If the primary Gemini model is unavailable or quota-limited,
            # automatically retry once with the lightweight Gemini model.
            print("[GEMINI PRIMARY ERROR]", primary_error)

            if MODEL != FAST_MODEL:
                print(f"[GEMINI FALLBACK] {FAST_MODEL}")
                reply = ask_gemini(
                    messages_for_model,
                    fast=True,
                    smart=False,
                )
            else:
                raise

        print(
            f"[TIMING] gemini="
            f"{time.perf_counter() - gemini_started:.3f}s"
        )

    except Exception as error:
        print(
            "[GEMINI ERROR]",
            error
        )

        error_text = str(error).lower()

        if "429" in error_text or "quota" in error_text or "resource_exhausted" in error_text:
            reply = (
                "Bhai, Gemini ka current quota limit hit ho gaya hai. "
                "Thodi der baad try karo."
            )
        else:
            reply = (
                "Sorry bhai, abhi AI backend se response nahi aa paaya."
            )

    # --------------------------------------------------------
    # STORE ASSISTANT RESPONSE
    # --------------------------------------------------------

    add_message(
        conversation,
        "assistant",
        reply
    )

    trim_messages(
        conversation
    )

    print(
        f"[TIMING] total_process_message="
        f"{time.perf_counter() - started_total:.3f}s"
    )

    return reply


# ============================================================
# DIRECT TEST
# ============================================================

if __name__ == "__main__":
    print(
        "Megatron core loaded successfully."
    )

    print(
        f"Fast model: {FAST_MODEL}"
    )

    print(
        f"Smart model: {MODEL}"
    )

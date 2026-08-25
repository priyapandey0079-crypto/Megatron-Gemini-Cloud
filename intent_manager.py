# ============================================================
# MEGATRON - INTENT MANAGER
# ============================================================

import re


# ============================================================
# NORMALIZE INPUT
# ============================================================

def normalize(text):

    return " ".join(
        text.strip().lower().split()
    )


# ============================================================
# CHECK MEMORY COMMAND
# ============================================================

def is_memory_command(text):

    text = normalize(text)

    commands = [
        "/remember ",
        "/memories",
        "/searchmemory ",
        "/forget ",
        "/update "
    ]

    return any(
        text.startswith(command)
        for command in commands
    )


# ============================================================
# DETECT MEMORY INTENT
# ============================================================

def detect_memory_intent(text):

    normalized = normalize(text)


    # --------------------------------------------------------
    # REMEMBER
    # --------------------------------------------------------

    remember_patterns = [
        r"\bremember that\b",
        r"\bremember this\b",
        r"\bdon't forget that\b",
        r"\bdont forget that\b",
        r"\byaad rakhna\b",
        r"\byaad rakh\b",
        r"\bise yaad rakhna\b"
    ]

    for pattern in remember_patterns:

        if re.search(
            pattern,
            normalized
        ):

            return "remember"


    # --------------------------------------------------------
    # SHOW MEMORY
    # --------------------------------------------------------

    memory_patterns = [
        r"\bwhat do you remember\b",
        r"\bwhat you remember\b",
        r"\bwhat do u remember\b",
        r"\bmeri memories\b",
        r"\bmeri memory\b",
        r"\byaad hai\b",
        r"\bkya yaad hai\b",
        r"\bdo you remember\b"
    ]

    for pattern in memory_patterns:

        if re.search(
            pattern,
            normalized
        ):

            return "show_memory"


    # --------------------------------------------------------
    # FORGET
    # --------------------------------------------------------

    forget_patterns = [
        r"\bforget that\b",
        r"\bforget this\b",
        r"\bforget my\b",
        r"\bforget it\b",
        r"\bbhool jao\b",
        r"\bbhool ja\b",
        r"\bise bhool jao\b"
    ]

    for pattern in forget_patterns:

        if re.search(
            pattern,
            normalized
        ):

            return "forget"


    return None


# ============================================================
# EXTRACT MEMORY CONTENT
# ============================================================

def extract_memory_content(text):

    original = text.strip()


    patterns = [

        r"remember that\s+(.+)",

        r"remember this\s*[:\-]?\s*(.+)",

        r"don't forget that\s+(.+)",

        r"dont forget that\s+(.+)",

        r"yaad rakhna\s+(.+)",

        r"yaad rakh\s+(.+)",

        r"ise yaad rakhna\s+(.+)"
    ]


    for pattern in patterns:

        match = re.search(
            pattern,
            original,
            re.IGNORECASE
        )


        if match:

            content = match.group(
                1
            ).strip()


            if content:

                return content


    return None


# ============================================================
# EXTRACT FORGET KEYWORD
# ============================================================

def extract_forget_keyword(text):

    original = text.strip()


    patterns = [

        r"forget that\s+(.+)",

        r"forget this\s+(.+)",

        r"forget my\s+(.+)",

        r"bhool jao\s+(.+)",

        r"bhool ja\s+(.+)",

        r"ise bhool jao\s+(.+)"
    ]


    for pattern in patterns:

        match = re.search(
            pattern,
            original,
            re.IGNORECASE
        )


        if match:

            keyword = match.group(
                1
            ).strip()


            if keyword:

                return keyword


    return None
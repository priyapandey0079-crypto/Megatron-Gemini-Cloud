# ============================================================
# MEGATRON - CONVERSATION CONTEXT MANAGER
# ============================================================

MAX_RECENT_MESSAGES = 12


# ============================================================
# ADD MESSAGE
# ============================================================

def add_message(messages, role, content):

    messages.append({
        "role": role,
        "content": content
    })


# ============================================================
# GET RECENT MESSAGES
# ============================================================

def get_recent_messages(messages, limit=MAX_RECENT_MESSAGES):

    if not messages:
        return []

    # Keep system prompt
    system_message = messages[0]

    # Everything after system
    conversation = messages[1:]

    # Keep only recent messages
    recent = conversation[-limit:]

    return [
        system_message,
        *recent
    ]


# ============================================================
# BUILD SMART CONTEXT
# ============================================================

def build_context(
    messages,
    memory_context="",
    current_user_message=""
):

    context = []

    # --------------------------------------------------------
    # SYSTEM PROMPT
    # --------------------------------------------------------

    if messages:

        context.append(messages[0])


    # --------------------------------------------------------
    # LONG-TERM MEMORY
    # --------------------------------------------------------

    if memory_context:

        context.append({
            "role": "system",
            "content": (
                "Relevant long-term memories about the user:\n\n"
                + memory_context
                + "\n\n"
                "Use these memories only when relevant. "
                "Do not mention the memory system."
            )
        })


    # --------------------------------------------------------
    # RECENT CONVERSATION
    # --------------------------------------------------------

    recent_messages = get_recent_messages(
        messages,
        MAX_RECENT_MESSAGES
    )

    for message in recent_messages[1:]:

        context.append(message)


    # --------------------------------------------------------
    # CURRENT MESSAGE
    # --------------------------------------------------------

    if current_user_message:

        already_exists = (
            context
            and context[-1]["role"] == "user"
            and context[-1]["content"] == current_user_message
        )

        if not already_exists:

            context.append({
                "role": "user",
                "content": current_user_message
            })


    return context


# ============================================================
# TRIM STORED CONVERSATION
# ============================================================

def trim_messages(
    messages,
    keep=MAX_RECENT_MESSAGES
):

    if len(messages) <= keep + 1:
        return messages

    system_message = messages[0]

    recent_messages = messages[-keep:]

    messages.clear()

    messages.append(system_message)

    messages.extend(recent_messages)

    return messages
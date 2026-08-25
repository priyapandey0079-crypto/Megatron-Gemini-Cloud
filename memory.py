import sqlite3
from datetime import datetime


# ============================================================
# CONFIG
# ============================================================

DB_NAME = "megatron_memory.db"


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():

    return sqlite3.connect(
        DB_NAME
    )


# ============================================================
# INITIALIZE DATABASE
# ============================================================

def init_memory():

    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            category TEXT NOT NULL,

            content TEXT NOT NULL,

            created_at TEXT NOT NULL,

            updated_at TEXT NOT NULL

        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# NORMALIZE TEXT
# ============================================================

def normalize_text(text):

    return " ".join(
        text.strip().lower().split()
    )


# ============================================================
# SAVE MEMORY
# ============================================================

def save_memory(
    content,
    category="general"
):

    content = content.strip()
    category = category.strip()


    if not content:

        return False


    if not category:

        category = "general"


    conn = get_connection()


    # --------------------------------------------------------
    # EXACT DUPLICATE CHECK
    # --------------------------------------------------------

    normalized_content = normalize_text(
        content
    )


    rows = conn.execute(
        """
        SELECT id, content
        FROM memories
        """
    ).fetchall()


    for memory_id, existing_content in rows:

        if normalize_text(
            existing_content
        ) == normalized_content:

            conn.close()

            return False


    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    now = datetime.now().isoformat()


    conn.execute(
        """
        INSERT INTO memories
        (
            category,
            content,
            created_at,
            updated_at
        )

        VALUES (?, ?, ?, ?)
        """,

        (
            category,
            content,
            now,
            now
        )
    )


    conn.commit()
    conn.close()


    return True


# ============================================================
# GET MEMORIES
# ============================================================

def get_memories(
    limit=20
):

    conn = get_connection()


    rows = conn.execute(
        """
        SELECT
            category,
            content

        FROM memories

        ORDER BY id DESC

        LIMIT ?
        """,

        (
            limit,
        )
    ).fetchall()


    conn.close()


    return rows


# ============================================================
# SEARCH MEMORIES
# ============================================================

def search_memories(
    keyword,
    limit=10
):

    keyword = keyword.strip()


    if not keyword:

        return []


    conn = get_connection()


    rows = conn.execute(
        """
        SELECT
            category,
            content

        FROM memories

        WHERE
            content LIKE ?
            OR category LIKE ?

        ORDER BY id DESC

        LIMIT ?
        """,

        (
            f"%{keyword}%",

            f"%{keyword}%",

            limit
        )
    ).fetchall()


    conn.close()


    return rows


# ============================================================
# DELETE MEMORY
# ============================================================

def delete_memory(
    keyword
):

    keyword = keyword.strip()


    if not keyword:

        return 0


    conn = get_connection()


    cursor = conn.execute(
        """
        DELETE FROM memories

        WHERE
            content LIKE ?
            OR category LIKE ?
        """,

        (
            f"%{keyword}%",

            f"%{keyword}%"
        )
    )


    deleted = cursor.rowcount


    conn.commit()
    conn.close()


    return deleted


# ============================================================
# UPDATE MEMORY
# ============================================================

def update_memory(
    old_keyword,
    new_content,
    category="general"
):

    old_keyword = old_keyword.strip()
    new_content = new_content.strip()
    category = category.strip()


    if not old_keyword:

        return False


    if not new_content:

        return False


    conn = get_connection()


    existing = conn.execute(
        """
        SELECT id

        FROM memories

        WHERE content LIKE ?

        ORDER BY id DESC

        LIMIT 1
        """,

        (
            f"%{old_keyword}%",
        )
    ).fetchone()


    if not existing:

        conn.close()

        return False


    memory_id = existing[0]


    # --------------------------------------------------------
    # PREVENT UPDATE TO SAME CONTENT
    # --------------------------------------------------------

    duplicate = conn.execute(
        """
        SELECT id

        FROM memories

        WHERE
            LOWER(content) = LOWER(?)
            AND id != ?
        LIMIT 1
        """,

        (
            new_content,
            memory_id
        )
    ).fetchone()


    if duplicate:

        conn.close()

        return False


    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    conn.execute(
        """
        UPDATE memories

        SET
            content = ?,
            category = ?,
            updated_at = ?

        WHERE id = ?
        """,

        (
            new_content,

            category,

            datetime.now().isoformat(),

            memory_id
        )
    )


    conn.commit()
    conn.close()


    return True


# ============================================================
# MEMORY CONTEXT
# ============================================================

def get_memory_context(
    limit=10
):

    memories = get_memories(
        limit
    )


    if not memories:

        return ""


    lines = [
        "Relevant memories about the user:"
    ]


    for category, content in memories:

        lines.append(
            f"- [{category}] {content}"
        )


    return "\n".join(
        lines
    )
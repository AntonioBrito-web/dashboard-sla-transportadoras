import bcrypt

from src.db import get_connection


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def get_user(username: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT id, username, password_hash, role, transportadora, email, deve_trocar_senha "
        "FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "password_hash": row[2],
        "role": row[3],
        "transportadora": row[4],
        "email": row[5],
        "deve_trocar_senha": bool(row[6]),
    }


def authenticate(username: str, password: str) -> dict | None:
    user = get_user(username)
    if not user or not verify_password(password, user["password_hash"]):
        return None
    return user


def create_user(
    username: str,
    password: str,
    role: str,
    transportadora: str | None = None,
    email: str | None = None,
    deve_trocar_senha: bool = False,
) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO users (username, password_hash, role, transportadora, email, deve_trocar_senha) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (username, hash_password(password), role, transportadora, email, 1 if deve_trocar_senha else 0),
    )
    conn.commit()
    conn.close()


def existing_transportadoras() -> set[str]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT transportadora FROM users WHERE role = 'transportadora'"
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


def existing_usernames() -> set[str]:
    conn = get_connection()
    rows = conn.execute("SELECT username FROM users").fetchall()
    conn.close()
    return {r[0] for r in rows}


def admin_exists() -> bool:
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    conn.close()
    return row is not None


def list_transportadora_users() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT username, transportadora, email FROM users WHERE role = 'transportadora' ORDER BY transportadora"
    ).fetchall()
    conn.close()
    return [{"username": r[0], "transportadora": r[1], "email": r[2] or ""} for r in rows]


def list_internal_users() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT username, role, email FROM users WHERE role IN ('admin', 'interno') ORDER BY role, username"
    ).fetchall()
    conn.close()
    return [{"username": r[0], "role": r[1], "email": r[2] or ""} for r in rows]


def set_password(username: str, new_password: str, deve_trocar_senha: bool | None = None) -> None:
    conn = get_connection()
    if deve_trocar_senha is None:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(new_password), username),
        )
    else:
        conn.execute(
            "UPDATE users SET password_hash = ?, deve_trocar_senha = ? WHERE username = ?",
            (hash_password(new_password), 1 if deve_trocar_senha else 0, username),
        )
    conn.commit()
    conn.close()


def set_email(username: str, email: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE users SET email = ? WHERE username = ?",
        (email.strip(), username),
    )
    conn.commit()
    conn.close()

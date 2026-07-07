import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'transportadora')),
            transportadora TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS justificativas (
            chave_viagem TEXT PRIMARY KEY,
            transportadora TEXT,
            justificativa TEXT DEFAULT '',
            anexo_nome TEXT DEFAULT '',
            anexo_caminho TEXT DEFAULT '',
            atualizado_por TEXT,
            atualizado_em TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    conn.close()


def get_justificativas(chaves: list[str]) -> dict:
    if not chaves:
        return {}
    conn = get_connection()
    placeholders = ",".join("?" for _ in chaves)
    rows = conn.execute(
        f"SELECT chave_viagem, justificativa, anexo_nome, anexo_caminho FROM justificativas "
        f"WHERE chave_viagem IN ({placeholders})",
        chaves,
    ).fetchall()
    conn.close()
    return {
        r[0]: {"justificativa": r[1] or "", "anexo_nome": r[2] or "", "anexo_caminho": r[3] or ""}
        for r in rows
    }


def salvar_justificativa_texto(chave_viagem: str, transportadora: str, texto: str, usuario: str) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO justificativas (chave_viagem, transportadora, justificativa, atualizado_por, atualizado_em)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(chave_viagem) DO UPDATE SET
            justificativa = excluded.justificativa,
            atualizado_por = excluded.atualizado_por,
            atualizado_em = excluded.atualizado_em
        """,
        (chave_viagem, transportadora, texto, usuario),
    )
    conn.commit()
    conn.close()


def salvar_justificativa_anexo(
    chave_viagem: str, transportadora: str, anexo_nome: str, anexo_caminho: str, usuario: str
) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO justificativas (chave_viagem, transportadora, anexo_nome, anexo_caminho, atualizado_por, atualizado_em)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(chave_viagem) DO UPDATE SET
            anexo_nome = excluded.anexo_nome,
            anexo_caminho = excluded.anexo_caminho,
            atualizado_por = excluded.atualizado_por,
            atualizado_em = excluded.atualizado_em
        """,
        (chave_viagem, transportadora, anexo_nome, anexo_caminho, usuario),
    )
    conn.commit()
    conn.close()

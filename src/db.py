import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

CATEGORIAS_APROVACAO = [
    "Atraso Transp 运输公司",
    "Atraso Incontrolável 不可控因素",
    "Atraso Operações 运营 SC/DC",
]


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def _migrar_tabela_users(conn: sqlite3.Connection) -> None:
    # SQLite não permite alterar CHECK constraints com ALTER TABLE, então
    # quando o banco existente ainda tem o CHECK antigo (sem 'interno') é
    # preciso reconstruir a tabela. Detecta isso lendo o SQL de criação
    # gravado no sqlite_master — só reconstrói se realmente precisar.
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if row is None or "'interno'" in row[0]:
        return
    colunas_existentes = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "email" not in colunas_existentes:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    conn.execute("ALTER TABLE users RENAME TO users_old")
    conn.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'transportadora', 'interno')),
            transportadora TEXT,
            email TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, transportadora, email, created_at) "
        "SELECT id, username, password_hash, role, transportadora, email, created_at FROM users_old"
    )
    conn.execute("DROP TABLE users_old")


def init_db() -> None:
    conn = get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'transportadora', 'interno')),
            transportadora TEXT,
            email TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    _migrar_tabela_users(conn)
    colunas_users = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "email" not in colunas_users:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS justificativas (
            chave_viagem TEXT PRIMARY KEY,
            transportadora TEXT,
            justificativa TEXT DEFAULT '',
            anexo_nome TEXT DEFAULT '',
            anexo_caminho TEXT DEFAULT '',
            atualizado_por TEXT,
            atualizado_em TEXT DEFAULT (datetime('now')),
            status_aprovacao TEXT DEFAULT 'pendente',
            categoria TEXT DEFAULT '',
            avaliado_por TEXT DEFAULT '',
            avaliado_em TEXT
        )
        """
    )
    # Migração leve para bancos já existentes criados antes destas colunas.
    colunas_existentes = {row[1] for row in conn.execute("PRAGMA table_info(justificativas)")}
    for coluna, definicao in [
        ("status_aprovacao", "TEXT DEFAULT 'pendente'"),
        ("categoria", "TEXT DEFAULT ''"),
        ("avaliado_por", "TEXT DEFAULT ''"),
        ("avaliado_em", "TEXT"),
    ]:
        if coluna not in colunas_existentes:
            conn.execute(f"ALTER TABLE justificativas ADD COLUMN {coluna} {definicao}")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def get_meta(chave: str) -> str | None:
    conn = get_connection()
    row = conn.execute("SELECT valor FROM app_meta WHERE chave = ?", (chave,)).fetchone()
    conn.close()
    return row[0] if row else None


def set_meta(chave: str, valor: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO app_meta (chave, valor) VALUES (?, ?) "
        "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (chave, valor),
    )
    conn.commit()
    conn.close()


def get_justificativas(chaves: list[str]) -> dict:
    if not chaves:
        return {}
    conn = get_connection()
    placeholders = ",".join("?" for _ in chaves)
    rows = conn.execute(
        f"SELECT chave_viagem, justificativa, anexo_nome, anexo_caminho, "
        f"status_aprovacao, categoria FROM justificativas WHERE chave_viagem IN ({placeholders})",
        chaves,
    ).fetchall()
    conn.close()
    return {
        r[0]: {
            "justificativa": r[1] or "",
            "anexo_nome": r[2] or "",
            "anexo_caminho": r[3] or "",
            "status_aprovacao": r[4] or "pendente",
            "categoria": r[5] or "",
        }
        for r in rows
    }


def salvar_justificativa_texto(chave_viagem: str, transportadora: str, texto: str, usuario: str) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO justificativas (chave_viagem, transportadora, justificativa, atualizado_por, atualizado_em, status_aprovacao, categoria)
        VALUES (?, ?, ?, ?, datetime('now'), 'pendente', '')
        ON CONFLICT(chave_viagem) DO UPDATE SET
            justificativa = excluded.justificativa,
            atualizado_por = excluded.atualizado_por,
            atualizado_em = excluded.atualizado_em,
            status_aprovacao = 'pendente',
            categoria = ''
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
        INSERT INTO justificativas (chave_viagem, transportadora, anexo_nome, anexo_caminho, atualizado_por, atualizado_em, status_aprovacao, categoria)
        VALUES (?, ?, ?, ?, ?, datetime('now'), 'pendente', '')
        ON CONFLICT(chave_viagem) DO UPDATE SET
            anexo_nome = excluded.anexo_nome,
            anexo_caminho = excluded.anexo_caminho,
            atualizado_por = excluded.atualizado_por,
            atualizado_em = excluded.atualizado_em,
            status_aprovacao = 'pendente',
            categoria = ''
        """,
        (chave_viagem, transportadora, anexo_nome, anexo_caminho, usuario),
    )
    conn.commit()
    conn.close()


def aprovar_justificativa(chave_viagem: str, categoria: str, usuario: str) -> None:
    conn = get_connection()
    conn.execute(
        """
        UPDATE justificativas
        SET status_aprovacao = 'aprovado', categoria = ?, avaliado_por = ?, avaliado_em = datetime('now')
        WHERE chave_viagem = ?
        """,
        (categoria, usuario, chave_viagem),
    )
    conn.commit()
    conn.close()


def reprovar_justificativa(chave_viagem: str, usuario: str) -> None:
    conn = get_connection()
    conn.execute(
        """
        UPDATE justificativas
        SET justificativa = '', anexo_nome = '', anexo_caminho = '',
            status_aprovacao = 'reprovado', categoria = '',
            avaliado_por = ?, avaliado_em = datetime('now')
        WHERE chave_viagem = ?
        """,
        (usuario, chave_viagem),
    )
    conn.commit()
    conn.close()


def chaves_reprovadas(transportadora: str) -> list[str]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT chave_viagem FROM justificativas WHERE transportadora = ? AND status_aprovacao = 'reprovado'",
        (transportadora,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def renomear_username(username_antigo: str, username_novo: str) -> None:
    conn = get_connection()
    conn.execute("UPDATE users SET username = ? WHERE username = ?", (username_novo, username_antigo))
    conn.commit()
    conn.close()

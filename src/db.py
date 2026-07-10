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
            deve_trocar_senha INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    _migrar_tabela_users(conn)
    colunas_users = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "email" not in colunas_users:
        conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "deve_trocar_senha" not in colunas_users:
        conn.execute("ALTER TABLE users ADD COLUMN deve_trocar_senha INTEGER NOT NULL DEFAULT 0")
    # A tabela justificativas NÃO mora mais aqui — foi movida pro Turso
    # (src/turso_db.py), banco externo persistente. O SQLite local é
    # efêmero (some a cada reboot/redeploy do Streamlit Cloud), e
    # justificativa/anexo são conteúdo único que não tem como recriar
    # sozinho — diferente de usuário/senha, que a gente recalcula.
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


def renomear_username(username_antigo: str, username_novo: str) -> None:
    conn = get_connection()
    conn.execute("UPDATE users SET username = ? WHERE username = ?", (username_novo, username_antigo))
    conn.commit()
    conn.close()

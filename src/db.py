import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"

CATEGORIAS_APROVACAO = [
    "Atraso Transportadora",
    "Atraso desconsiderado",
]


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db() -> None:
    # Usuários e justificativas moram no Turso (src/turso_db.py), banco
    # externo persistente — o disco local aqui é efêmero (some a cada
    # reboot/redeploy do Streamlit Cloud). Só sobra local o app_meta, que
    # guarda flags idempotentes (ex.: "já rodei essa migração") — perder
    # isso num wipe não é grave, a flag só volta a ser aplicada de novo.
    conn = get_connection()
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

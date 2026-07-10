def _get_connection():
    # Justificativas e anexos ficam num banco Turso (libSQL) externo, não no
    # SQLite local do container — o disco local é efêmero: some por completo
    # a cada reboot/redeploy do Streamlit Cloud (mesmo sem clique manual em
    # "Reboot app"). Diferente das contas de usuário (que dá pra recriar
    # sozinhas com senha determinística), justificativa e anexo são
    # conteúdo único que não tem como "recalcular" — por isso precisam de
    # armazenamento persistente de verdade, não só de uma mitigação.
    #
    # O import de libsql e a leitura dos secrets ficam DENTRO da função de
    # propósito: se o pacote não estiver instalado ou os secrets não
    # estiverem configurados, só a funcionalidade de justificativa/anexo
    # fica indisponível (com mensagem clara) — o resto do app (login,
    # dashboard, etc.) continua funcionando normalmente.
    try:
        import libsql
        import streamlit as st

        database = str(st.secrets.get("TURSO_DATABASE_URL", "")).strip()
        auth_token = str(st.secrets.get("TURSO_AUTH_TOKEN", "")).strip()
    except Exception as e:
        raise RuntimeError(f"Falha ao preparar conexão com o Turso: {e}") from e
    if not database or not auth_token:
        raise RuntimeError(
            "TURSO_DATABASE_URL / TURSO_AUTH_TOKEN não configurados nos Secrets do "
            "Streamlit Cloud. Sem isso, justificativas e anexos não têm onde ser "
            "salvos de forma persistente — configure antes de usar o sistema."
        )
    return libsql.connect(database=database, auth_token=auth_token)


def init_justificativas_db() -> None:
    conn = _get_connection()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS justificativas (
            chave_viagem TEXT PRIMARY KEY,
            transportadora TEXT,
            justificativa TEXT DEFAULT '',
            anexo_nome TEXT DEFAULT '',
            anexo_bytes BLOB,
            atualizado_por TEXT,
            atualizado_em TEXT DEFAULT (datetime('now')),
            status_aprovacao TEXT DEFAULT 'pendente',
            categoria TEXT DEFAULT '',
            avaliado_por TEXT DEFAULT '',
            avaliado_em TEXT
        )
        """
    )
    # E-mail cadastrado também mora aqui, não no SQLite local — senão some
    # a cada wipe (recriação de conta) e o usuário é obrigado a recadastrar
    # o e-mail toda vez que o disco local zera, mesmo já tendo cadastrado
    # antes.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS emails_cadastrados (
            username TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            atualizado_em TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    conn.close()


def get_email(username: str) -> str:
    conn = _get_connection()
    row = conn.execute(
        "SELECT email FROM emails_cadastrados WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    return row[0] if row else ""


def set_email(username: str, email: str) -> None:
    email = email.strip()
    conn = _get_connection()
    if email:
        conn.execute(
            "INSERT INTO emails_cadastrados (username, email, atualizado_em) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(username) DO UPDATE SET email = excluded.email, atualizado_em = excluded.atualizado_em",
            (username, email),
        )
    else:
        conn.execute("DELETE FROM emails_cadastrados WHERE username = ?", (username,))
    conn.commit()
    conn.close()


def get_justificativas(chaves: list[str]) -> dict:
    # Não traz anexo_bytes aqui de propósito — essa lista alimenta a tabela
    # inteira, e puxar o BLOB de cada linha pela rede toda hora deixaria a
    # tela lenta à toa. O conteúdo do anexo só é buscado sob demanda, em
    # get_anexo(), quando alguém realmente clica pra ver/baixar um.
    if not chaves:
        return {}
    conn = _get_connection()
    placeholders = ",".join("?" for _ in chaves)
    rows = conn.execute(
        f"SELECT chave_viagem, justificativa, anexo_nome, "
        f"status_aprovacao, categoria FROM justificativas WHERE chave_viagem IN ({placeholders})",
        chaves,
    ).fetchall()
    conn.close()
    return {
        r[0]: {
            "justificativa": r[1] or "",
            "anexo_nome": r[2] or "",
            "status_aprovacao": r[3] or "pendente",
            "categoria": r[4] or "",
        }
        for r in rows
    }


def get_anexo(chave_viagem: str) -> tuple[str, bytes] | None:
    conn = _get_connection()
    row = conn.execute(
        "SELECT anexo_nome, anexo_bytes FROM justificativas WHERE chave_viagem = ?",
        (chave_viagem,),
    ).fetchone()
    conn.close()
    if not row or not row[1]:
        return None
    return row[0], row[1]


def salvar_justificativa_texto(chave_viagem: str, transportadora: str, texto: str, usuario: str) -> None:
    conn = _get_connection()
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
    chave_viagem: str, transportadora: str, anexo_nome: str, anexo_bytes: bytes, usuario: str
) -> None:
    conn = _get_connection()
    conn.execute(
        """
        INSERT INTO justificativas (chave_viagem, transportadora, anexo_nome, anexo_bytes, atualizado_por, atualizado_em, status_aprovacao, categoria)
        VALUES (?, ?, ?, ?, ?, datetime('now'), 'pendente', '')
        ON CONFLICT(chave_viagem) DO UPDATE SET
            anexo_nome = excluded.anexo_nome,
            anexo_bytes = excluded.anexo_bytes,
            atualizado_por = excluded.atualizado_por,
            atualizado_em = excluded.atualizado_em,
            status_aprovacao = 'pendente',
            categoria = ''
        """,
        (chave_viagem, transportadora, anexo_nome, anexo_bytes, usuario),
    )
    conn.commit()
    conn.close()


def aprovar_justificativa(chave_viagem: str, categoria: str, usuario: str) -> None:
    conn = _get_connection()
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
    conn = _get_connection()
    conn.execute(
        """
        UPDATE justificativas
        SET justificativa = '', anexo_nome = '', anexo_bytes = NULL,
            status_aprovacao = 'reprovado', categoria = '',
            avaliado_por = ?, avaliado_em = datetime('now')
        WHERE chave_viagem = ?
        """,
        (usuario, chave_viagem),
    )
    conn.commit()
    conn.close()


def excluir_justificativa(chave_viagem: str) -> None:
    # Diferente de reprovar (que zera o conteúdo mas mantém a linha com
    # status "reprovado"), isso apaga o registro inteiro — usado pra tirar
    # de vez dados de teste/engano do banco, não faz parte do fluxo normal
    # de aprovação.
    conn = _get_connection()
    conn.execute("DELETE FROM justificativas WHERE chave_viagem = ?", (chave_viagem,))
    conn.commit()
    conn.close()


def chaves_reprovadas(transportadora: str) -> list[str]:
    conn = _get_connection()
    rows = conn.execute(
        "SELECT chave_viagem FROM justificativas WHERE transportadora = ? AND status_aprovacao = 'reprovado'",
        (transportadora,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]

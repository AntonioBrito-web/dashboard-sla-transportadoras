import base64
import json

# Justificativas, anexos e e-mails cadastrados ficam num banco Turso
# (libSQL) externo, não no SQLite local do container — o disco local é
# efêmero: some por completo a cada reboot/redeploy do Streamlit Cloud
# (mesmo sem clique manual em "Reboot app"). Diferente das contas de
# usuário (que dá pra recriar sozinhas com senha determinística), esse
# conteúdo é único e não tem como "recalcular" — por isso precisa de
# armazenamento persistente de verdade.
#
# Usa a API HTTP do Turso (protocolo Hrana v2/pipeline) via `requests`
# em vez do pacote `libsql` — esse pacote usa uma extensão nativa em
# Rust que causou um segmentation fault em produção (derrubando o
# processo inteiro do Streamlit, não só a funcionalidade de
# justificativa). `requests` já é dependência do projeto e é puro
# HTTP, sem esse risco.


def _config() -> tuple[str, str]:
    try:
        import streamlit as st

        database = str(st.secrets.get("TURSO_DATABASE_URL", "")).strip()
        auth_token = str(st.secrets.get("TURSO_AUTH_TOKEN", "")).strip()
    except Exception as e:
        raise RuntimeError(f"Falha ao ler configuração do Turso: {e}") from e
    if not database or not auth_token:
        raise RuntimeError(
            "TURSO_DATABASE_URL / TURSO_AUTH_TOKEN não configurados nos Secrets do "
            "Streamlit Cloud. Sem isso, justificativas e anexos não têm onde ser "
            "salvos de forma persistente — configure antes de usar o sistema."
        )
    url = database.replace("libsql://", "https://", 1).rstrip("/") + "/v2/pipeline"
    return url, auth_token


def _arg(valor):
    if valor is None:
        return {"type": "null"}
    if isinstance(valor, bool):
        return {"type": "integer", "value": str(int(valor))}
    if isinstance(valor, int):
        return {"type": "integer", "value": str(valor)}
    if isinstance(valor, float):
        return {"type": "float", "value": valor}
    if isinstance(valor, (bytes, bytearray)):
        return {"type": "blob", "base64": base64.b64encode(bytes(valor)).decode("ascii")}
    return {"type": "text", "value": str(valor)}


def _valor(item: dict):
    tipo = item.get("type")
    if tipo == "null":
        return None
    if tipo == "integer":
        return int(item["value"])
    if tipo == "float":
        return float(item["value"])
    if tipo == "blob":
        b64 = (item.get("base64") or item.get("value") or "").strip()
        if not b64:
            return b""
        # Corrige padding ausente defensivamente — alguns caminhos de
        # transporte JSON removem os "=" finais do base64.
        faltando = len(b64) % 4
        if faltando:
            b64 += "=" * (4 - faltando)
        return base64.b64decode(b64)
    return item.get("value")


def _executar(sql: str, args: list | None = None) -> dict:
    import requests

    url, auth_token = _config()
    payload = {
        "requests": [
            {"type": "execute", "stmt": {"sql": sql, "args": [_arg(a) for a in (args or [])]}},
            {"type": "close"},
        ]
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=20,
    )
    resp.raise_for_status()
    corpo = resp.json()
    primeiro = corpo["results"][0]
    if primeiro.get("type") == "error":
        erro = primeiro.get("error", {})
        raise RuntimeError(f"Erro Turso: {erro.get('message', erro)}")
    resultado = primeiro["response"]["result"]
    linhas = [[_valor(v) for v in linha] for linha in resultado.get("rows", [])]
    return {"linhas": linhas}


def init_justificativas_db() -> None:
    _executar(
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
    _executar(
        """
        CREATE TABLE IF NOT EXISTS emails_cadastrados (
            username TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            atualizado_em TEXT DEFAULT (datetime('now'))
        )
        """
    )


def get_email(username: str) -> str:
    resultado = _executar("SELECT email FROM emails_cadastrados WHERE username = ?", [username])
    return resultado["linhas"][0][0] if resultado["linhas"] else ""


def set_email(username: str, email: str) -> None:
    email = email.strip()
    if email:
        _executar(
            "INSERT INTO emails_cadastrados (username, email, atualizado_em) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(username) DO UPDATE SET email = excluded.email, atualizado_em = excluded.atualizado_em",
            [username, email],
        )
    else:
        _executar("DELETE FROM emails_cadastrados WHERE username = ?", [username])


def get_justificativas(chaves: list[str]) -> dict:
    # Não traz anexo_bytes aqui de propósito — essa lista alimenta a tabela
    # inteira, e puxar o BLOB de cada linha pela rede toda hora deixaria a
    # tela lenta à toa. O conteúdo do anexo só é buscado sob demanda, em
    # get_anexo(), quando alguém realmente clica pra ver/baixar um.
    if not chaves:
        return {}
    placeholders = ",".join("?" for _ in chaves)
    resultado = _executar(
        f"SELECT chave_viagem, justificativa, anexo_nome, "
        f"status_aprovacao, categoria FROM justificativas WHERE chave_viagem IN ({placeholders})",
        chaves,
    )
    return {
        r[0]: {
            "justificativa": r[1] or "",
            "anexo_nome": r[2] or "",
            "status_aprovacao": r[3] or "pendente",
            "categoria": r[4] or "",
        }
        for r in resultado["linhas"]
    }


def get_anexo(chave_viagem: str) -> tuple[str, bytes] | None:
    resultado = _executar(
        "SELECT anexo_nome, anexo_bytes FROM justificativas WHERE chave_viagem = ?", [chave_viagem]
    )
    if not resultado["linhas"] or not resultado["linhas"][0][1]:
        return None
    nome, dados = resultado["linhas"][0]
    return nome, dados


def salvar_justificativa_texto(chave_viagem: str, transportadora: str, texto: str, usuario: str) -> None:
    _executar(
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
        [chave_viagem, transportadora, texto, usuario],
    )


def salvar_justificativa_anexo(
    chave_viagem: str, transportadora: str, anexo_nome: str, anexo_bytes: bytes, usuario: str
) -> None:
    _executar(
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
        [chave_viagem, transportadora, anexo_nome, anexo_bytes, usuario],
    )


def aprovar_justificativa(chave_viagem: str, categoria: str, usuario: str) -> None:
    _executar(
        """
        UPDATE justificativas
        SET status_aprovacao = 'aprovado', categoria = ?, avaliado_por = ?, avaliado_em = datetime('now')
        WHERE chave_viagem = ?
        """,
        [categoria, usuario, chave_viagem],
    )


def reprovar_justificativa(chave_viagem: str, usuario: str) -> None:
    _executar(
        """
        UPDATE justificativas
        SET justificativa = '', anexo_nome = '', anexo_bytes = NULL,
            status_aprovacao = 'reprovado', categoria = '',
            avaliado_por = ?, avaliado_em = datetime('now')
        WHERE chave_viagem = ?
        """,
        [usuario, chave_viagem],
    )


def excluir_justificativa(chave_viagem: str) -> None:
    # Diferente de reprovar (que zera o conteúdo mas mantém a linha com
    # status "reprovado"), isso apaga o registro inteiro — usado pra tirar
    # de vez dados de teste/engano do banco, não faz parte do fluxo normal
    # de aprovação.
    _executar("DELETE FROM justificativas WHERE chave_viagem = ?", [chave_viagem])


def chaves_reprovadas(transportadora: str) -> list[str]:
    resultado = _executar(
        "SELECT chave_viagem FROM justificativas WHERE transportadora = ? AND status_aprovacao = 'reprovado'",
        [transportadora],
    )
    return [r[0] for r in resultado["linhas"]]

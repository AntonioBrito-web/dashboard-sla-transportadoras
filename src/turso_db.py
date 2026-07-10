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

        database = str(st.secrets.get("TURSO_DATABASE_URL", "")).strip().strip("'\"")
        auth_token = str(st.secrets.get("TURSO_AUTH_TOKEN", "")).strip().strip("'\"")
    except Exception as e:
        raise RuntimeError(f"Falha ao ler configuração do Turso: {e}") from e
    if not database or not auth_token:
        raise RuntimeError(
            "TURSO_DATABASE_URL / TURSO_AUTH_TOKEN não configurados nos Secrets do "
            "Streamlit Cloud. Sem isso, justificativas e anexos não têm onde ser "
            "salvos de forma persistente — configure antes de usar o sistema."
        )
    # Normaliza pra https://, aceitando o valor vir como libsql://, https://
    # ou só o hostname puro (sem esquema) — e evita barra dupla antes do
    # /v2/pipeline não importa como o secret foi colado.
    if database.startswith("libsql://"):
        database = "https://" + database[len("libsql://") :]
    elif not database.startswith("https://") and not database.startswith("http://"):
        database = "https://" + database
    url = database.rstrip("/") + "/v2/pipeline"
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
    if not resp.ok:
        raise RuntimeError(
            f"HTTP {resp.status_code} do Turso ao chamar {url!r}. "
            f"Corpo da resposta: {resp.text[:300]!r}"
        )
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


def init_usuarios_db() -> None:
    # Contas de login (admin/transportadora/interno) também vivem aqui, não
    # mais no SQLite local — é isso que garante que a senha (e o e-mail)
    # sobrevivem a um reboot/redeploy: a conta só é recriada com a senha
    # padrão se realmente não existir ainda, nunca por já existir e o disco
    # local ter zerado.
    _executar(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            transportadora TEXT,
            email TEXT,
            deve_trocar_senha INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def get_usuario(username: str) -> dict | None:
    resultado = _executar(
        "SELECT username, password_hash, role, transportadora, email, deve_trocar_senha "
        "FROM usuarios WHERE username = ?",
        [username],
    )
    if not resultado["linhas"]:
        return None
    r = resultado["linhas"][0]
    return {
        "username": r[0],
        "password_hash": r[1],
        "role": r[2],
        "transportadora": r[3],
        "email": r[4] or "",
        "deve_trocar_senha": bool(r[5]),
    }


def criar_usuario(
    username: str,
    password_hash: str,
    role: str,
    transportadora: str | None = None,
    email: str | None = None,
    deve_trocar_senha: bool = False,
) -> None:
    _executar(
        "INSERT INTO usuarios (username, password_hash, role, transportadora, email, deve_trocar_senha) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [username, password_hash, role, transportadora, email, 1 if deve_trocar_senha else 0],
    )


def atualizar_senha_usuario(username: str, password_hash: str, deve_trocar_senha: bool | None = None) -> None:
    if deve_trocar_senha is None:
        _executar("UPDATE usuarios SET password_hash = ? WHERE username = ?", [password_hash, username])
    else:
        _executar(
            "UPDATE usuarios SET password_hash = ?, deve_trocar_senha = ? WHERE username = ?",
            [password_hash, 1 if deve_trocar_senha else 0, username],
        )


def usuarios_existentes() -> set[str]:
    resultado = _executar("SELECT username FROM usuarios")
    return {r[0] for r in resultado["linhas"]}


def transportadoras_existentes() -> set[str]:
    resultado = _executar("SELECT transportadora FROM usuarios WHERE role = 'transportadora'")
    return {r[0] for r in resultado["linhas"] if r[0]}


def admin_existe() -> bool:
    resultado = _executar("SELECT 1 FROM usuarios WHERE role = 'admin' LIMIT 1")
    return bool(resultado["linhas"])


def listar_transportadora_usuarios() -> list[dict]:
    resultado = _executar(
        "SELECT username, transportadora, email FROM usuarios WHERE role = 'transportadora' ORDER BY transportadora"
    )
    return [{"username": r[0], "transportadora": r[1], "email": r[2] or ""} for r in resultado["linhas"]]


def listar_usuarios_internos() -> list[dict]:
    resultado = _executar(
        "SELECT username, role, email FROM usuarios WHERE role IN ('admin', 'interno') ORDER BY role, username"
    )
    return [{"username": r[0], "role": r[1], "email": r[2] or ""} for r in resultado["linhas"]]


def renomear_usuario(username_antigo: str, username_novo: str) -> None:
    _executar("UPDATE usuarios SET username = ? WHERE username = ?", [username_novo, username_antigo])


def get_email(username: str) -> str:
    resultado = _executar("SELECT email FROM usuarios WHERE username = ?", [username])
    return resultado["linhas"][0][0] if resultado["linhas"] and resultado["linhas"][0][0] else ""


def set_email(username: str, email: str) -> None:
    _executar("UPDATE usuarios SET email = ? WHERE username = ?", [email.strip(), username])


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

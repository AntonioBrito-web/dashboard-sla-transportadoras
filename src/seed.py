import csv
import hashlib
import secrets
import string
import unicodedata
from pathlib import Path

from src.auth import (
    admin_exists,
    create_user,
    existing_transportadoras,
    existing_usernames,
    list_transportadora_users,
    set_password,
)
from src.data import load_transportadoras, load_transportadoras_com_abreviatura
from src.db import renomear_username
from src.email_util import enviar_email

ROOT = Path(__file__).resolve().parent.parent
ADMIN_CRED_FILE = ROOT / "admin_credentials.txt"
TRANSPORTADORA_CRED_FILE = ROOT / "credenciais_transportadoras.csv"
INTERNOS_CRED_FILE = ROOT / "credenciais_internos.csv"

# Lista fixa pedida pelo usuário: pessoal interno com acesso "como admin"
# (visualização completa, sem gerenciar senhas nem editar justificativas) e
# duas pessoas com acesso admin pleno (igual à conta admin original).
USUARIOS_INTERNOS_SEED = [
    ("Carlos Vinicius de Souza Oliveira", "interno"),
    ("SILMARA CAETANO DE BARROS", "interno"),
    ("FERNANDA PEREIRA DA SILVA", "interno"),
    ("Leandro Ramos", "interno"),
    ("DIEGO SOUSA SANTOS", "interno"),
    ("JONATAN NASCIMENTO", "interno"),
    ("DOUGLAS GOMES ALVES", "interno"),
    ("ANA CARLA DE JESUS MARQUES", "admin"),
    ("Antonio Carlos Ramos Brito", "admin"),
]


def slugify(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ascii", "ignore").decode("ascii").lower().strip()
    slug_chars = [ch if ch.isalnum() else "_" for ch in ascii_name]
    slug = "".join(slug_chars)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_") or "transportadora"


def gen_password(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _segredo_senha() -> str:
    # Lido do secret SEED_SECRET no Streamlit Cloud (Settings -> Secrets).
    # Sugestão: usar uma data fixa no formato ddmmaaaa (ex.: "09072026").
    # Fallback embutido garante que a semeadura automática continue
    # funcionando mesmo antes do secret ser configurado — mas o segredo
    # real só fica seguro depois de configurado nos Secrets (não commitado).
    try:
        import streamlit as st

        valor = str(st.secrets.get("SEED_SECRET", "")).strip()
    except Exception:
        valor = ""
    return valor or "JTEXPRESS-SLA-PADRAO"


def senha_padrao(username: str, length: int = 10) -> str:
    # Senha determinística: mesmo username + mesmo SEED_SECRET sempre
    # gera a mesma senha. É o que garante que, se o Streamlit Cloud zerar
    # o disco (redeploy ou reboot), as contas recriadas automaticamente
    # (admin, transportadoras, internos) voltem com a MESMA senha de
    # sempre — sem depender de log nem de redistribuir credencial nova.
    segredo = _segredo_senha()
    alphabet = string.ascii_letters + string.digits
    senha_chars: list[str] = []
    contador = 0
    while len(senha_chars) < length:
        digest = hashlib.sha256(f"{segredo}:{username}:{contador}".encode("utf-8")).digest()
        for b in digest:
            if len(senha_chars) >= length:
                break
            senha_chars.append(alphabet[b % len(alphabet)])
        contador += 1
    return "".join(senha_chars)


def _username_pessoa(nome: str) -> str:
    # Padrão pedido: primeiro_nome + "_" + ultimo_nome (ignora nomes do meio).
    partes = [p for p in nome.strip().split() if p]
    if not partes:
        return slugify(nome)
    primeiro = partes[0]
    ultimo = partes[-1] if len(partes) > 1 else partes[0]
    return f"{slugify(primeiro)}_{slugify(ultimo)}"


def ensure_admin() -> str | None:
    if admin_exists():
        return None
    password = senha_padrao("admin", 14)
    create_user("admin", password, "admin")
    # Log sempre (visível nos logs do Streamlit Cloud); arquivo é só
    # conveniência local — se o disco for somente leitura, ignora.
    print(f"[seed] Conta admin criada. usuario=admin senha={password}", flush=True)
    try:
        ADMIN_CRED_FILE.write_text(f"usuario: admin\nsenha: {password}\n", encoding="utf-8")
    except OSError:
        pass
    # Retorna a senha (em vez de só logar) para que o app.py possa exibi-la
    # na própria tela de login — se o disco for zerado num reboot e isto
    # recriar o admin do zero, ninguém fica sem saber a senha nova.
    return password


def reset_admin_password() -> str:
    nova_senha = senha_padrao("admin", 14)
    if admin_exists():
        set_password("admin", nova_senha)
    else:
        create_user("admin", nova_senha, "admin")
    print(f"[seed] Senha do admin redefinida. usuario=admin senha={nova_senha}", flush=True)
    try:
        ADMIN_CRED_FILE.write_text(f"usuario: admin\nsenha: {nova_senha}\n", encoding="utf-8")
    except OSError:
        pass
    return nova_senha


def reset_transportadora_password(username: str) -> str:
    return reset_user_password(username)


def _username_transportadora(nome: str, mapa_abreviatura: dict) -> str:
    abreviatura = mapa_abreviatura.get(nome)
    base = f"{slugify(abreviatura)}_logistica" if abreviatura else slugify(nome)
    return base


def ensure_transportadora_accounts() -> list[dict]:
    ja_cadastradas = existing_transportadoras()
    usernames = existing_usernames()

    transportadoras = load_transportadoras()
    novas = [t for t in transportadoras if t not in ja_cadastradas]

    if not novas:
        return []

    mapa_abreviatura = load_transportadoras_com_abreviatura()

    novos_registros = []
    for nome in novas:
        base_slug = _username_transportadora(nome, mapa_abreviatura)
        slug = base_slug
        i = 1
        while slug in usernames:
            i += 1
            slug = f"{base_slug}{i}"
        usernames.add(slug)

        senha = senha_padrao(slug)
        create_user(slug, senha, "transportadora", transportadora=nome, deve_trocar_senha=True)
        novos_registros.append({"transportadora": nome, "usuario": slug, "senha": senha})
        print(f"[seed] Conta transportadora criada. usuario={slug} senha={senha} transportadora={nome}", flush=True)

    try:
        arquivo_novo = not TRANSPORTADORA_CRED_FILE.exists()
        with open(TRANSPORTADORA_CRED_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["transportadora", "usuario", "senha"])
            if arquivo_novo:
                writer.writeheader()
            writer.writerows(novos_registros)
    except OSError:
        pass

    return novos_registros


def ensure_usuarios_internos() -> list[dict]:
    # Roda a cada boot (dentro de seed_all), igual às contas de
    # transportadora: como a senha agora é determinística (senha_padrao),
    # não tem problema recriar sozinho depois de um wipe — a senha volta
    # sendo sempre a mesma, sem precisar expor nada novo em tela pública.
    # O botão "Criar contas padrão da lista" no admin continua existindo
    # como atalho manual (idempotente, útil se quiser forçar/conferir).
    usernames_existentes = existing_usernames()
    novos = []
    for nome, role in USUARIOS_INTERNOS_SEED:
        username = _username_pessoa(nome)
        if username in usernames_existentes:
            continue  # já semeado numa rodada anterior
        senha = senha_padrao(username)
        create_user(username, senha, role, transportadora=None, deve_trocar_senha=True)
        usernames_existentes.add(username)
        novos.append({"nome": nome, "usuario": username, "senha": senha, "role": role})
        print(f"[seed] Conta interna criada. usuario={username} role={role} nome={nome}", flush=True)

    if novos:
        try:
            arquivo_novo = not INTERNOS_CRED_FILE.exists()
            with open(INTERNOS_CRED_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["nome", "usuario", "senha", "role"])
                if arquivo_novo:
                    writer.writeheader()
                writer.writerows(novos)
        except OSError:
            pass

    return novos


def criar_acesso_interno(nome: str, role: str, email: str = "") -> dict:
    # Cadastro avulso pelo painel do admin — usa o mesmo padrão de username
    # (primeiro_ultimo), com sufixo numérico só em caso de colisão real.
    usernames_existentes = existing_usernames()
    base = _username_pessoa(nome)
    username = base
    i = 1
    while username in usernames_existentes:
        i += 1
        username = f"{base}{i}"
    senha = gen_password(10)
    create_user(username, senha, role, transportadora=None, email=email.strip() or None, deve_trocar_senha=True)
    print(f"[seed] Conta interna criada (cadastro avulso). usuario={username} role={role} nome={nome}", flush=True)
    return {"nome": nome, "usuario": username, "senha": senha, "role": role}


def reset_user_password(username: str) -> str:
    nova_senha = gen_password(10)
    set_password(username, nova_senha, deve_trocar_senha=True)
    print(f"[seed] Senha redefinida. usuario={username} senha={nova_senha}", flush=True)
    return nova_senha


def padronizar_usernames_transportadora() -> int:
    # Renomeia contas já existentes para o padrão abreviatura_logistica,
    # preservando senha/hash — só troca o username usado no login.
    mapa_abreviatura = load_transportadoras_com_abreviatura()
    usuarios = list_transportadora_users()
    usernames_existentes = existing_usernames()

    renomeados = 0
    for u in usuarios:
        username_antigo = u["username"]
        nome = u["transportadora"]
        abreviatura = mapa_abreviatura.get(nome)
        if not abreviatura:
            continue
        base_slug = f"{slugify(abreviatura)}_logistica"
        if base_slug == username_antigo:
            continue

        usernames_existentes.discard(username_antigo)
        novo_username = base_slug
        i = 1
        while novo_username in usernames_existentes:
            i += 1
            novo_username = f"{base_slug}{i}"
        usernames_existentes.add(novo_username)

        renomear_username(username_antigo, novo_username)
        print(f"[seed] Username padronizado: {username_antigo} -> {novo_username} ({nome})", flush=True)
        renomeados += 1

    return renomeados


def notificar_contas_recriadas(novas_transportadoras: list[dict], novos_internos: list[dict]) -> bool:
    # Manda UM e-mail consolidado pra um endereço fixo (NOTIFY_EMAIL nos
    # Secrets), em vez de um e-mail por pessoa: os e-mails individuais
    # ficam guardados no mesmo SQLite que é zerado no wipe, então depois
    # de um wipe não sobra pra quem mandar aviso individual mesmo. O admin
    # (usuario "admin") não entra nessa lista — tem o fluxo próprio via
    # RESET_ADMIN e não precisa de e-mail cadastrado.
    todos = list(novas_transportadoras) + list(novos_internos)
    if not todos:
        return False

    try:
        import streamlit as st

        destino = str(st.secrets.get("NOTIFY_EMAIL", "")).strip()
    except Exception:
        destino = ""
    if not destino:
        print("[email] NOTIFY_EMAIL não configurado — pulando aviso de contas recriadas.", flush=True)
        return False

    linhas = [
        f"- {item.get('transportadora') or item.get('nome') or item['usuario']} "
        f"— usuário: {item['usuario']} — senha: {item['senha']}"
        for item in todos
    ]
    corpo = (
        "O banco de contas do Dashboard SLA Transportadoras foi recriado "
        "(provavelmente um redeploy/reboot no Streamlit Cloud zerou o disco).\n\n"
        f"{len(todos)} conta(s) foram recriadas com a senha padrão de sempre:\n\n"
        + "\n".join(linhas)
        + "\n\nCada usuário será obrigado a trocar a senha no próximo login."
    )
    return enviar_email(destino, f"[Dashboard SLA] {len(todos)} conta(s) recriada(s) após reboot", corpo)


def seed_all() -> str | None:
    senha_admin_criada = ensure_admin()
    novas_transportadoras = ensure_transportadora_accounts()
    novos_internos = ensure_usuarios_internos()
    notificar_contas_recriadas(novas_transportadoras, novos_internos)
    return senha_admin_criada

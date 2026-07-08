import csv
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
    password = gen_password(14)
    create_user("admin", password, "admin")
    # Log sempre (visível nos logs do Streamlit Cloud); arquivo é só
    # conveniência local — se o disco for somente leitura, ignora.
    print(f"[seed] Conta admin criada. usuario=admin senha={password}")
    try:
        ADMIN_CRED_FILE.write_text(f"usuario: admin\nsenha: {password}\n", encoding="utf-8")
    except OSError:
        pass
    # Retorna a senha (em vez de só logar) para que o app.py possa exibi-la
    # na própria tela de login — se o disco for zerado num reboot e isto
    # recriar o admin do zero, ninguém fica sem saber a senha nova.
    return password


def reset_admin_password() -> str:
    nova_senha = gen_password(14)
    if admin_exists():
        set_password("admin", nova_senha)
    else:
        create_user("admin", nova_senha, "admin")
    print(f"[seed] Senha do admin redefinida. usuario=admin senha={nova_senha}")
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

        senha = gen_password(10)
        create_user(slug, senha, "transportadora", transportadora=nome)
        novos_registros.append({"transportadora": nome, "usuario": slug, "senha": senha})
        print(f"[seed] Conta transportadora criada. usuario={slug} senha={senha} transportadora={nome}")

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
    # Diferente de ensure_transportadora_accounts, esta função NÃO roda
    # sozinha a cada boot do app — é disparada pelo admin pelo painel
    # "Gerenciar acessos internos". Motivo: as senhas geradas só podem
    # aparecer numa tela que exige login (o botão do admin), nunca na tela
    # de login pública como acontece com a senha do admin bootstrap.
    usernames_existentes = existing_usernames()
    novos = []
    for nome, role in USUARIOS_INTERNOS_SEED:
        username = _username_pessoa(nome)
        if username in usernames_existentes:
            continue  # já semeado numa rodada anterior
        senha = gen_password(10)
        create_user(username, senha, role, transportadora=None)
        usernames_existentes.add(username)
        novos.append({"nome": nome, "usuario": username, "senha": senha, "role": role})
        print(f"[seed] Conta interna criada. usuario={username} role={role} nome={nome}")

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
    create_user(username, senha, role, transportadora=None, email=email.strip() or None)
    print(f"[seed] Conta interna criada (cadastro avulso). usuario={username} role={role} nome={nome}")
    return {"nome": nome, "usuario": username, "senha": senha, "role": role}


def reset_user_password(username: str) -> str:
    nova_senha = gen_password(10)
    set_password(username, nova_senha)
    print(f"[seed] Senha redefinida. usuario={username} senha={nova_senha}")
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
        print(f"[seed] Username padronizado: {username_antigo} -> {novo_username} ({nome})")
        renomeados += 1

    return renomeados


def seed_all() -> str | None:
    senha_admin_criada = ensure_admin()
    ensure_transportadora_accounts()
    return senha_admin_criada

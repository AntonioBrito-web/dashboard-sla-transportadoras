import csv
import secrets
import string
import unicodedata
from pathlib import Path

from src.auth import admin_exists, create_user, existing_transportadoras, existing_usernames, set_password
from src.data import load_transportadoras

ROOT = Path(__file__).resolve().parent.parent
ADMIN_CRED_FILE = ROOT / "admin_credentials.txt"
TRANSPORTADORA_CRED_FILE = ROOT / "credenciais_transportadoras.csv"


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


def ensure_admin() -> None:
    if admin_exists():
        return
    password = gen_password(14)
    create_user("admin", password, "admin")
    # Log sempre (visível nos logs do Streamlit Cloud); arquivo é só
    # conveniência local — se o disco for somente leitura, ignora.
    print(f"[seed] Conta admin criada. usuario=admin senha={password}")
    try:
        ADMIN_CRED_FILE.write_text(f"usuario: admin\nsenha: {password}\n", encoding="utf-8")
    except OSError:
        pass


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


def ensure_transportadora_accounts() -> list[dict]:
    ja_cadastradas = existing_transportadoras()
    usernames = existing_usernames()

    transportadoras = load_transportadoras()
    novas = [t for t in transportadoras if t not in ja_cadastradas]

    if not novas:
        return []

    novos_registros = []
    for nome in novas:
        base_slug = slugify(nome)
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


def seed_all() -> None:
    ensure_admin()
    ensure_transportadora_accounts()

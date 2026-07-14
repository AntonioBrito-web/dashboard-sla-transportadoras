import bcrypt

from src.turso_db import (
    admin_existe,
    alterar_role_usuario,
    criar_usuario,
    get_usuario,
    listar_todos_usuarios,
    listar_transportadora_usuarios,
    listar_usuarios_internos,
    renomear_usuario,
    transportadoras_existentes,
    usuarios_existentes,
    atualizar_senha_usuario,
)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def get_user(username: str) -> dict | None:
    return get_usuario(username)


def authenticate(username: str, password: str) -> dict | None:
    user = get_user(username)
    if not user or not verify_password(password, user["password_hash"]):
        return None
    return user


def create_user(
    username: str,
    password: str,
    role: str,
    transportadora: str | None = None,
    email: str | None = None,
    deve_trocar_senha: bool = False,
) -> None:
    criar_usuario(username, hash_password(password), role, transportadora, email, deve_trocar_senha)


def existing_transportadoras() -> set[str]:
    return transportadoras_existentes()


def existing_usernames() -> set[str]:
    return usuarios_existentes()


def admin_exists() -> bool:
    return admin_existe()


def list_transportadora_users() -> list[dict]:
    return listar_transportadora_usuarios()


def list_internal_users() -> list[dict]:
    return listar_usuarios_internos()


def list_all_users() -> list[dict]:
    return listar_todos_usuarios()


def set_user_role(username: str, novo_role: str, nova_transportadora: str | None = None) -> None:
    alterar_role_usuario(username, novo_role, nova_transportadora)


def set_password(username: str, new_password: str, deve_trocar_senha: bool | None = None) -> None:
    atualizar_senha_usuario(username, hash_password(new_password), deve_trocar_senha)


def renomear_username(username_antigo: str, username_novo: str) -> None:
    renomear_usuario(username_antigo, username_novo)

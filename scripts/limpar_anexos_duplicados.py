import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.turso_db import encontrar_anexos_duplicados, init_justificativas_db, remover_anexos_por_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Encontra anexos duplicados (mesma viagem + mesmo nome + mesmo "
            "conteúdo) na tabela anexos do Turso. Por padrão só mostra o que "
            "seria removido (dry-run) — passe --apply pra remover de verdade."
        )
    )
    parser.add_argument("--apply", action="store_true", help="Remove de fato. Sem essa flag é só dry-run.")
    parser.add_argument(
        "--id-viagem",
        default="",
        help="Restringe a viagens cujo ID Viagem seja este (ex.: DKGX26071100008). "
        "Sem essa opção, varre a tabela inteira.",
    )
    args = parser.parse_args()

    # Precisa rodar antes de qualquer consulta: garante que a tabela
    # 'anexos' já existe (mesma checagem feita pelo app no boot).
    init_justificativas_db()

    duplicados = encontrar_anexos_duplicados()
    if args.id_viagem:
        duplicados = [d for d in duplicados if d["chave_viagem"].split("|")[0] == args.id_viagem]

    if not duplicados:
        print("Nenhum anexo duplicado encontrado.")
        return

    total_remover = 0
    for grupo in duplicados:
        print(
            f"Viagem {grupo['chave_viagem']!r} — arquivo {grupo['nome']!r}: "
            f"mantendo id={grupo['manter_id']}, removendo ids={grupo['remover_ids']}"
        )
        total_remover += len(grupo["remover_ids"])

    print(f"\nTotal de anexos duplicados encontrados: {total_remover}")

    if not args.apply:
        print("Modo dry-run — nada foi removido. Rode de novo com --apply para remover de verdade.")
        return

    for grupo in duplicados:
        remover_anexos_por_id(grupo["remover_ids"])
    print(f"Removidos {total_remover} anexo(s) duplicado(s).")


if __name__ == "__main__":
    main()

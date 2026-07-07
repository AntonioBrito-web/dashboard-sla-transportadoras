import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import init_db
from src.seed import seed_all


def main() -> None:
    init_db()
    seed_all()
    print("Concluído.")


if __name__ == "__main__":
    main()

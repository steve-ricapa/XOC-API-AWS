from __future__ import annotations

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional local dependency during bootstrap
    def load_dotenv() -> bool:
        return False

from database_report_loader import test_database_connection


def main() -> None:
    load_dotenv()
    test_database_connection()
    print("Conexion BD OK")


if __name__ == "__main__":
    main()

from src.persistence.db import get_engine
from src.persistence.models import Base


def main() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    print("Schema bootstrap completed")


if __name__ == "__main__":
    main()

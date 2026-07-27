"""Creează (sau resetează parola) unui utilizator administrator local.

Rulare:  .venv/Scripts/python.exe scripts/create_admin.py <username> <parola>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models.auth import User  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.security import create_user, hash_password  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        print("Utilizare: create_admin.py <username> <parola>")
        raise SystemExit(1)
    username, password = sys.argv[1], sys.argv[2]

    session = SessionLocal()
    try:
        existing = session.scalar(select(User).where(User.username == username))
        if existing:
            existing.password_hash = hash_password(password)
            existing.rol = UserRole.ADMINISTRATOR
            existing.activ = True
            print(f"Parola resetată pentru utilizatorul existent '{username}'.")
        else:
            create_user(session, username, password, rol=UserRole.ADMINISTRATOR)
            print(f"Utilizator administrator '{username}' creat.")
        session.commit()
    finally:
        session.close()


if __name__ == "__main__":
    main()

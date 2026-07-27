from app.models.enums import UserRole
from app.security import authenticate, create_user, hash_password, verify_password


def test_hash_and_verify_password_roundtrip():
    hashed = hash_password("parola-secreta")
    assert hashed != "parola-secreta"
    assert verify_password("parola-secreta", hashed) is True
    assert verify_password("gresita", hashed) is False


def test_create_user_and_authenticate(db_session):
    create_user(db_session, "admin", "parola123", rol=UserRole.ADMINISTRATOR)

    user = authenticate(db_session, "admin", "parola123")
    assert user is not None
    assert user.rol == UserRole.ADMINISTRATOR

    assert authenticate(db_session, "admin", "gresita") is None
    assert authenticate(db_session, "nu-exista", "orice") is None


def test_inactive_user_cannot_authenticate(db_session):
    user = create_user(db_session, "consultant", "parola123", rol=UserRole.CONSULTARE)
    user.activ = False
    db_session.flush()

    assert authenticate(db_session, "consultant", "parola123") is None

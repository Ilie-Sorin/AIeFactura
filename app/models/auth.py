import datetime as dt

from sqlalchemy import CheckConstraint, DateTime, String, Boolean, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.enums import UserRole


class User(Base):
    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint("rol IN ('administrator', 'consultare')", name="rol_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    rol: Mapped[str] = mapped_column(String(20), default=UserRole.CONSULTARE)
    activ: Mapped[bool] = mapped_column(Boolean, default=True)
    creat_la: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    ultima_autentificare: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

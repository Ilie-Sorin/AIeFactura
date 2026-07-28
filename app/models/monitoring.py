import datetime as dt

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class IntegrityAlert(Base):
    """Alertă de completitudine/integritate (cap. 8). Nu se șterge niciodată
    (cap. „Convenții de cod": nimic nu se șterge din interfață) — doar se
    marchează rezolvată, manual sau automat, când condiția dispare la o
    rulare ulterioară. `cheie` identifică stabil PROBLEMA (nu rândul din
    tabel) în interiorul unui `cod`, ex. `invoice:123` sau `cif=185.../serie=FCT`,
    ca să nu se creeze alerte duplicate pentru aceeași cauză încă deschisă."""

    __tablename__ = "integrity_alert"
    __table_args__ = (
        CheckConstraint("nivel IN ('info', 'avertisment', 'critic')", name="nivel_valid"),
        Index(
            "uq_integrity_alert_deschisa",
            "cod",
            "cheie",
            unique=True,
            postgresql_where=text("rezolvat_la IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cod: Mapped[str] = mapped_column(String(50), index=True)
    nivel: Mapped[str] = mapped_column(String(15))
    cheie: Mapped[str] = mapped_column(String(255))
    mesaj: Mapped[str] = mapped_column(Text)
    detalii: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    generat_la: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    actualizat_la: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    rezolvat_la: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rezolvat_automat: Mapped[bool | None] = mapped_column(nullable=True)
    rezolvat_de_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.id"), nullable=True)
    motiv_rezolvare: Mapped[str | None] = mapped_column(Text, nullable=True)

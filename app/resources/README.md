# Stylesheet ANAF (vizualizare RO-CIUS)

Acest director trebuie să conțină `anaf_stylesheet.xsl` — foaia de stil XSLT
oficială publicată de ANAF pentru vizualizarea facturilor electronice
RO-CIUS/EN 16931 (aceeași folosită de aplicația oficială de vizualizare a
facturii electronice).

**Fișierul nu este distribuit în acest depozit** — e proprietatea ANAF, se
obține separat (portalul ANAF / SPV) și se copiază manual aici, sau la calea
indicată de `ANAF_STYLESHEET_PATH` din `.env`.

Fără acest fișier, ecranul „Vizualizare” al unui document arată un mesaj
explicit că stylesheet-ul nu e configurat — nu se folosește un șablon
propriu în locul lui (cap. 3: „nu cu șablon propriu").

Odată adăugat fișierul, transformarea XML → HTML se face cu `lxml`
(`app/services/pdf.py`); PDF-ul propriu-zis se obține din browser
(Print → Salvează ca PDF) — vezi acel modul pentru motivul acestei alegeri.

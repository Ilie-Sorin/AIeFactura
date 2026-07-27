from enum import StrEnum


class BatchType(StrEnum):
    SCAN_LOCAL = "scan_local"
    ANAF = "anaf"
    IMPORT_EXTERN = "import_extern"


class BatchStatus(StrEnum):
    IN_CURS = "in_curs"
    TERMINAT = "terminat"
    TERMINAT_CU_ERORI = "terminat_cu_erori"
    ANULAT = "anulat"


class SourceObjectType(StrEnum):
    ZIP = "zip"
    XML_FACTURA = "xml_factura"
    XML_SEMNATURA = "xml_semnatura"
    ATASAMENT = "atasament"
    PDF = "pdf"


class Direction(StrEnum):
    INTRARE = "intrare"
    IESIRE = "iesire"


class DocumentState(StrEnum):
    PRIMIT = "primit"
    PARSAT = "parsat"
    NORMALIZAT = "normalizat"
    VALIDAT = "validat"
    INDEXAT = "indexat"
    EROARE = "eroare"


class PartyRole(StrEnum):
    FURNIZOR = "furnizor"
    CLIENT = "client"
    REPREZENTANT_FISCAL = "reprezentant_fiscal"


class RelationSource(StrEnum):
    XML = "xml"
    REGULA = "regula"
    MANUAL = "manual"


class RelationState(StrEnum):
    CONFIRMATA = "confirmata"
    PROPUSA = "propusa"
    RESPINSA = "respinsa"


class ReconciliationResultState(StrEnum):
    NOUA = "noua"
    IN_LUCRU = "in_lucru"
    REZOLVATA = "rezolvata"
    ACCEPTATA_CA_DIFERENTA = "acceptata_ca_diferenta"
    IGNORATA = "ignorata"


class UserRole(StrEnum):
    ADMINISTRATOR = "administrator"
    CONSULTARE = "consultare"

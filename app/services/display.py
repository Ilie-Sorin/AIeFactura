"""Helper de afișare pentru ecranele care listează facturi: atașează numele
furnizorului/clientului (nu doar CIF-ul) pe obiecte `Invoice` deja încărcate
cu `.parts` — CIF-ul e un cod de identificare, nu ceva ce un utilizator
recunoaște la o privire; numele e ce contează într-o listă."""

from app.models.document import Invoice


def attach_party_names(invoices: list[Invoice]) -> None:
    for invoice in invoices:
        nume_furnizor = None
        nume_client = None
        for p in invoice.parts:
            if p.rol == "furnizor" and p.denumire:
                nume_furnizor = p.denumire
            elif p.rol == "client" and p.denumire:
                nume_client = p.denumire
        invoice.nume_furnizor = nume_furnizor
        invoice.nume_client = nume_client

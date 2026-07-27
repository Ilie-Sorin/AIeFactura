-- Rol de execuție al aplicației, distinct de proprietarul schemei (folosit de Alembic).
-- Motiv: GRANT/REVOKE nu au efect asupra proprietarului unui tabel — imutabilitatea
-- lui source_object (cap. 3: „doar INSERT, impus prin GRANT, nu prin convenție”)
-- cere ca aplicația să se conecteze cu un rol separat, fără privilegii de owner.
CREATE ROLE aiefactura_app LOGIN PASSWORD 'aiefactura_app_dev';

GRANT CONNECT ON DATABASE aiefactura TO aiefactura_app;
GRANT USAGE ON SCHEMA public TO aiefactura_app;

-- Se aplică automat pe orice tabel/secvență creat ulterior de rolul proprietar
-- (aiefactura, cel folosit de migrările Alembic) — nu trebuie reluat la fiecare migrare.
ALTER DEFAULT PRIVILEGES FOR ROLE aiefactura IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aiefactura_app;
ALTER DEFAULT PRIVILEGES FOR ROLE aiefactura IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO aiefactura_app;

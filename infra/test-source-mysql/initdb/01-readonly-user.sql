-- Least-privilege account for RealitySync.
--
-- The account a customer should create for us, reproduced here so the
-- connector is tested against the permissions it will actually have. Running
-- as root during development would hide exactly the failures that matter — a
-- discovery query needing a privilege the reader does not have.
--
-- REQUIRE SSL is the MySQL equivalent of the source-postgres hostssl rules:
-- the server itself refuses an unencrypted session for this account, so the
-- connector's TLS requirement is verified end to end rather than trusted.

CREATE USER 'realitysync_reader'@'%'
    IDENTIFIED BY 'change-me-locally'
    REQUIRE SSL;

-- Look, nothing else. SELECT covers information_schema access for the tables
-- the account can read, which is what discovery needs.
GRANT SELECT ON source_demo.* TO 'realitysync_reader'@'%';

FLUSH PRIVILEGES;

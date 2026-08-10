-- Least-privilege role for RealitySync.
--
-- This is the role a customer should create for us, reproduced here so the
-- connector is developed and tested against the permissions it will actually
-- have in production rather than against a superuser. Running as owner during
-- development hides exactly the failures that matter: a discovery query that
-- needs a privilege the reader does not have.
--
-- The same statements appear in docs/phase-3-postgres-connector.md for
-- customers to run against their own database.

CREATE ROLE realitysync_reader WITH LOGIN PASSWORD 'change-me-locally';

-- Connect and look, nothing else.
GRANT CONNECT ON DATABASE source_demo TO realitysync_reader;
GRANT USAGE ON SCHEMA public TO realitysync_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO realitysync_reader;

-- Tables created later are covered too, so onboarding a new table does not
-- require another grant.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO realitysync_reader;

-- Explicitly withhold write access. Redundant given the grants above, but it
-- documents the intent and survives someone widening the defaults later.
REVOKE CREATE ON SCHEMA public FROM realitysync_reader;

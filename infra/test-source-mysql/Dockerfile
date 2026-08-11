# ---------------------------------------------------------------------------
# Disposable MySQL used as a *source* database for local development.
#
# THIS IS TEST INFRASTRUCTURE, NOT PRODUCT DATA.
#
# Exists for the same reason as test-source-postgres: the connector must be
# developed against a real server, over TLS, with the least privilege a
# customer would actually grant. A second *type* of source also turns the
# claim "connectors can be added without touching core logic" into something
# demonstrated rather than asserted.
#
# MySQL 8 generates a self-signed certificate at initialisation and serves TLS
# on it, so no certificate work is needed here — unlike PostgreSQL, which
# needs the files present before the server starts. The reader account is
# created with REQUIRE SSL, so the server refuses an unencrypted session for
# that user and "TLS is required" is enforced by the source as well as by the
# connector.
# ---------------------------------------------------------------------------
FROM mysql:8.4

# Creates the least-privilege reader on first initialisation.
COPY initdb/ /docker-entrypoint-initdb.d/

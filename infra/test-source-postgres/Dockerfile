# ---------------------------------------------------------------------------
# Disposable PostgreSQL used as a *source* database for local development.
#
# THIS IS TEST INFRASTRUCTURE, NOT PRODUCT DATA.
#
# RealitySync connects to it the same way it would connect to a customer's
# database: over TCP, with TLS, using credentials the operator supplies. It is
# deliberately a separate server from the application database — the connector
# must have no privileged path to it, and sharing a server would hide that.
#
# TLS matters here. The connector refuses sslmode=disable, so a source database
# without TLS could not be used to verify the connector end to end. A
# self-signed certificate is generated at build time, which is why the
# development connection uses sslmode=require (encrypted, certificate not
# verified) rather than verify-full. A real deployment against a managed
# provider should use verify-full.
# ---------------------------------------------------------------------------
FROM postgres:16-alpine

RUN apk add --no-cache openssl

# Generated at build time so the files exist before postgres starts. initdb.d
# scripts run too late: the server is already up by then.
RUN mkdir -p /etc/postgres-tls \
    && openssl req -new -x509 -days 3650 -nodes \
        -out /etc/postgres-tls/server.crt \
        -keyout /etc/postgres-tls/server.key \
        -subj "/CN=source-postgres" \
        -addext "subjectAltName=DNS:source-postgres,DNS:localhost,IP:127.0.0.1" \
    && chmod 600 /etc/postgres-tls/server.key \
    && chown postgres:postgres /etc/postgres-tls/server.key /etc/postgres-tls/server.crt

# hostssl-only rules: this server refuses unencrypted connections outright, so
# "TLS is required" is enforced by the source as well as by the connector.
COPY pg_hba.conf /etc/postgres-tls/pg_hba.conf
RUN chown postgres:postgres /etc/postgres-tls/pg_hba.conf \
    && chmod 600 /etc/postgres-tls/pg_hba.conf

# Creates the least-privilege reader role on first initialisation, so the
# connector is exercised with the permissions a customer would actually grant.
COPY initdb/ /docker-entrypoint-initdb.d/

CMD [ \
  "postgres", \
  "-c", "ssl=on", \
  "-c", "ssl_cert_file=/etc/postgres-tls/server.crt", \
  "-c", "ssl_key_file=/etc/postgres-tls/server.key", \
  "-c", "hba_file=/etc/postgres-tls/pg_hba.conf" \
]

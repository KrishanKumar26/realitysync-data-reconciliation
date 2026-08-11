import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AddSourceForm } from "@/components/sources/add-source-form";
import { SchemaExplorer } from "@/components/sources/schema-explorer";
import { SourceStatusBadge } from "@/components/sources/status-badge";
import SourcesPage from "@/app/sources/page";

import { authenticatedSession, renderWithSession, stubApi } from "./helpers";

const SESSION = { "/api/auth/session": { body: authenticatedSession() } };

const SOURCE = {
  id: "src-1",
  name: "Production warehouse",
  kind: "postgresql",
  status: "connected",
  connection: {
    host: "db.example.com",
    port: 5432,
    database: "warehouse",
    username: "realitysync_reader",
    ssl_mode: "require",
    password_set: true,
  },
  last_connected_at: "2026-08-10T10:00:00Z",
  last_connection_latency_ms: 12,
  last_synced_at: "2026-08-10T10:05:00Z",
  last_error: null,
  last_error_at: null,
  stream_count: 1,
  observation_count: 42,
  created_at: "2026-08-01T00:00:00Z",
};

describe("Sources page", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("offers to connect a database when none exist", async () => {
    stubApi({ ...SESSION, "/api/data-sources": { body: [] } });

    await renderWithSession(<SourcesPage />);

    expect(await screen.findByText("No sources connected")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Add source" }),
    ).toBeInTheDocument();
  });

  it("shows real counts from the API, not placeholders", async () => {
    stubApi({ ...SESSION, "/api/data-sources": { body: [SOURCE] } });

    await renderWithSession(<SourcesPage />);

    const link = await screen.findByRole("link", {
      name: /Production warehouse/,
    });
    expect(within(link).getByText("42")).toBeInTheDocument();
    // The source type leads the line now that more than one kind exists:
    // "which system is this" is the first thing an operator needs, and
    // host:port alone does not answer it.
    expect(
      within(link).getByText(
        "PostgreSQL · db.example.com:5432/warehouse · require",
      ),
    ).toBeInTheDocument();
  });

  it("surfaces a load failure rather than showing an empty list", async () => {
    // An error rendered as "no sources" would suggest the workspace is empty
    // when it may not be.
    stubApi({
      ...SESSION,
      "/api/data-sources": {
        status: 500,
        body: {
          error: { code: "INTERNAL_ERROR", message: "Database unavailable." },
        },
      },
    });

    await renderWithSession(<SourcesPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load sources",
    );
    expect(screen.queryByText("No sources connected")).not.toBeInTheDocument();
  });
});

describe("SourceStatusBadge", () => {
  it("distinguishes stored credentials from a proven connection", async () => {
    // The distinction the product turns on: "configured" must never be
    // presented as "connected".
    stubApi(SESSION);
    await renderWithSession(
      <>
        <SourceStatusBadge status="configured" />
        <SourceStatusBadge status="connected" />
      </>,
    );

    expect(screen.getByText("Not yet tested")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toBeInTheDocument();
  });
});

describe("AddSourceForm", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("offers no way to disable TLS", async () => {
    // 'disable', 'allow' and 'prefer' are absent rather than present and
    // rejected: an option you can pick and then be told off for is worse than
    // one that was never offered.
    stubApi(SESSION);

    await renderWithSession(
      <AddSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );

    expect(screen.getByRole("radio", { name: /require/ })).toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: /verify-full/ }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("radio", { name: /disable/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("radio", { name: /prefer/ }),
    ).not.toBeInTheDocument();
  });

  it("offers both source types and moves the port with the choice", async () => {
    // A connector nobody can select is not shipped. The port default has to
    // follow the type: leaving 5432 in place for MySQL would send every new
    // MySQL source at PostgreSQL's port.
    const user = userEvent.setup();
    stubApi(SESSION);

    await renderWithSession(
      <AddSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );

    expect(screen.getByLabelText("Port")).toHaveValue(5432);

    await user.click(screen.getByRole("radio", { name: /MySQL/ }));
    expect(screen.getByLabelText("Port")).toHaveValue(3306);

    await user.click(screen.getByRole("radio", { name: /PostgreSQL/ }));
    expect(screen.getByLabelText("Port")).toHaveValue(5432);
  });

  it("keeps a port the operator typed when the source type changes", async () => {
    // Moving the default is helpful; overwriting a deliberate choice is not.
    const user = userEvent.setup();
    stubApi(SESSION);

    await renderWithSession(
      <AddSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );

    const port = screen.getByLabelText("Port");
    await user.clear(port);
    await user.type(port, "6543");

    await user.click(screen.getByRole("radio", { name: /MySQL/ }));

    expect(port).toHaveValue(6543);
  });

  it("sends the chosen source type", async () => {
    const user = userEvent.setup();
    const { calls } = stubApi({
      ...SESSION,
      "/api/data-sources": { status: 201, body: { ...SOURCE, kind: "mysql" } },
    });

    await renderWithSession(
      <AddSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );

    await user.click(screen.getByRole("radio", { name: /MySQL/ }));
    await user.type(screen.getByLabelText("Source name"), "Billing");
    await user.type(screen.getByLabelText("Host"), "mysql.example.com");
    await user.type(screen.getByLabelText("Database"), "billing");
    await user.type(screen.getByLabelText("Username"), "reader");
    await user.type(screen.getByLabelText("Password"), "source-secret-value");
    await user.click(screen.getByRole("button", { name: "Save source" }));

    await waitFor(() => {
      const request = calls.find(
        (call) =>
          call.method === "POST" && call.url.endsWith("/api/data-sources"),
      );
      expect(request).toBeDefined();
      const body = request?.body as Record<string, unknown> & {
        connection: Record<string, unknown>;
      };
      expect(body.kind).toBe("mysql");
      expect(body.connection.port).toBe(3306);
      // The TLS requirement is identical across source types.
      expect(body.connection.ssl_mode).toBe("require");
    });
  });

  it("sends the connection details and requires TLS", async () => {
    const user = userEvent.setup();
    const { calls } = stubApi({
      ...SESSION,
      "/api/data-sources": { status: 201, body: SOURCE },
    });

    await renderWithSession(
      <AddSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );

    await user.type(
      screen.getByLabelText("Source name"),
      "Production warehouse",
    );
    await user.type(screen.getByLabelText("Host"), "db.example.com");
    await user.type(screen.getByLabelText("Database"), "warehouse");
    await user.type(screen.getByLabelText("Username"), "realitysync_reader");
    await user.type(screen.getByLabelText("Password"), "source-secret-value");
    await user.click(screen.getByRole("button", { name: "Save source" }));

    await waitFor(() => {
      const request = calls.find(
        (call) =>
          call.method === "POST" && call.url.endsWith("/api/data-sources"),
      );
      expect(request).toBeDefined();
      const body = request?.body as { connection: Record<string, unknown> };
      expect(body.connection.host).toBe("db.example.com");
      expect(body.connection.ssl_mode).toBe("require");
      expect(body.connection.password).toBe("source-secret-value");
    });
  });

  it("clears the password from component state after submitting", async () => {
    // It has been sent and is never needed again; React state is readable
    // from a devtools session.
    const user = userEvent.setup();
    stubApi({ ...SESSION, "/api/data-sources": { status: 201, body: SOURCE } });

    await renderWithSession(
      <AddSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );

    await user.type(screen.getByLabelText("Source name"), "Warehouse");
    await user.type(screen.getByLabelText("Host"), "db.example.com");
    await user.type(screen.getByLabelText("Database"), "warehouse");
    await user.type(screen.getByLabelText("Username"), "reader");
    const password = screen.getByLabelText("Password");
    await user.type(password, "source-secret-value");
    await user.click(screen.getByRole("button", { name: "Save source" }));

    await waitFor(() => expect(password).toHaveValue(""));
  });

  it("reports a rejected connection without echoing what was typed", async () => {
    const user = userEvent.setup();
    stubApi({
      ...SESSION,
      "/api/data-sources": {
        status: 400,
        body: {
          error: {
            code: "BAD_REQUEST",
            message: "The database rejected the username or password.",
          },
        },
      },
    });

    await renderWithSession(
      <AddSourceForm onCreated={() => {}} onCancel={() => {}} />,
    );

    await user.type(screen.getByLabelText("Source name"), "Warehouse");
    await user.type(screen.getByLabelText("Host"), "db.example.com");
    await user.type(screen.getByLabelText("Database"), "warehouse");
    await user.type(screen.getByLabelText("Username"), "reader");
    await user.type(screen.getByLabelText("Password"), "unique-secret-42");
    await user.click(screen.getByRole("button", { name: "Save source" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "The database rejected the username or password.",
    );
    expect(alert.textContent).not.toContain("unique-secret-42");
  });
});

const DISCOVERY = {
  schemas: ["public"],
  inaccessible_schemas: [],
  discovered_at: "2026-08-10T10:00:00Z",
  tables: [
    {
      schema_name: "public",
      table_name: "shipments",
      qualified_name: "public.shipments",
      kind: "table",
      approximate_row_count: 1200,
      primary_key_columns: ["shipment_id"],
      temporal_columns: ["updated_at"],
      configured: false,
      columns: [
        {
          name: "shipment_id",
          data_type: "bigint",
          nullable: false,
          is_primary_key: true,
          is_temporal: false,
        },
        {
          name: "updated_at",
          data_type: "timestamp with time zone",
          nullable: false,
          is_primary_key: false,
          is_temporal: true,
        },
      ],
    },
  ],
};

describe("SchemaExplorer", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not read the schema until asked", async () => {
    // Discovery dials a customer's production database; doing it on page load
    // would be rude and slow.
    const { calls } = stubApi(SESSION);

    await renderWithSession(
      <SchemaExplorer sourceId="src-1" onStreamCreated={() => {}} />,
    );

    expect(screen.getByText("Schema not read yet")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("discover-schema"))).toBe(false);
  });

  it("labels row counts as approximate", async () => {
    // They are planner estimates. Presenting an estimate as exact would be
    // precisely the unverified claim this product exists to eliminate.
    const user = userEvent.setup();
    stubApi({ ...SESSION, "/discover-schema": { body: DISCOVERY } });

    await renderWithSession(
      <SchemaExplorer sourceId="src-1" onStreamCreated={() => {}} />,
    );
    await user.click(screen.getByRole("button", { name: "Discover schema" }));

    expect(await screen.findByText(/approx\. 1,200 rows/)).toBeInTheDocument();
  });

  it("asks what the timestamp means before configuring a stream", async () => {
    const user = userEvent.setup();
    stubApi({ ...SESSION, "/discover-schema": { body: DISCOVERY } });

    await renderWithSession(
      <SchemaExplorer sourceId="src-1" onStreamCreated={() => {}} />,
    );
    await user.click(screen.getByRole("button", { name: "Discover schema" }));
    await user.click(await screen.findByRole("button", { name: "Configure" }));

    expect(
      screen.getByText(/What does this table's timestamp mean\?/),
    ).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Observed/ })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Recorded/ })).toBeInTheDocument();
    expect(
      screen.getByRole("radio", { name: /No time column/ }),
    ).toBeInTheDocument();
  });

  it("sends the chosen event-time semantics", async () => {
    const user = userEvent.setup();
    const { calls } = stubApi({
      ...SESSION,
      "/discover-schema": { body: DISCOVERY },
      "/streams": { status: 201, body: { id: "stream-1" } },
    });

    await renderWithSession(
      <SchemaExplorer sourceId="src-1" onStreamCreated={() => {}} />,
    );
    await user.click(screen.getByRole("button", { name: "Discover schema" }));
    await user.click(await screen.findByRole("button", { name: "Configure" }));
    await user.click(screen.getByRole("radio", { name: /Observed/ }));
    await user.click(screen.getByRole("button", { name: "Configure stream" }));

    await waitFor(() => {
      const request = calls.find((call) => call.url.endsWith("/streams"));
      expect(request?.body).toMatchObject({
        schema_name: "public",
        table_name: "shipments",
        primary_key_columns: ["shipment_id"],
        event_time_semantics: "observed",
        event_time_column: "updated_at",
      });
    });
  });

  it("will not offer a table with no primary key", async () => {
    // Without a stable identity every row would look like a new thing.
    const user = userEvent.setup();
    stubApi({
      ...SESSION,
      "/discover-schema": {
        body: {
          ...DISCOVERY,
          tables: [
            {
              ...DISCOVERY.tables[0],
              table_name: "events_log",
              qualified_name: "public.events_log",
              primary_key_columns: [],
            },
          ],
        },
      },
    });

    await renderWithSession(
      <SchemaExplorer sourceId="src-1" onStreamCreated={() => {}} />,
    );
    await user.click(screen.getByRole("button", { name: "Discover schema" }));

    expect(await screen.findByText("No primary key")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Configure" }),
    ).not.toBeInTheDocument();
  });

  it("reports schemas the role cannot read instead of hiding them", async () => {
    // "You cannot see this" is actionable; silently omitting it looks
    // identical to the schema not existing.
    const user = userEvent.setup();
    stubApi({
      ...SESSION,
      "/discover-schema": {
        body: { ...DISCOVERY, inaccessible_schemas: ["billing", "hr"] },
      },
    });

    await renderWithSession(
      <SchemaExplorer sourceId="src-1" onStreamCreated={() => {}} />,
    );
    await user.click(screen.getByRole("button", { name: "Discover schema" }));

    expect(await screen.findByText(/billing, hr/)).toBeInTheDocument();
  });

  it("shows an actionable message when discovery is refused", async () => {
    const user = userEvent.setup();
    stubApi({
      ...SESSION,
      "/discover-schema": {
        status: 400,
        body: {
          error: {
            code: "BAD_REQUEST",
            message:
              "The database role does not have permission for this operation. Grant USAGE on the schema.",
          },
        },
      },
    });

    await renderWithSession(
      <SchemaExplorer sourceId="src-1" onStreamCreated={() => {}} />,
    );
    await user.click(screen.getByRole("button", { name: "Discover schema" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Grant USAGE on the schema",
    );
  });
});

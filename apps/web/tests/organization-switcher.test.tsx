import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OrganizationSwitcher } from "@/components/auth/organization-switcher";

import {
  authenticatedSession,
  organization,
  renderWithSession,
  stubApi,
} from "./helpers";

const NORTHWIND = organization({
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  name: "Northwind Logistics",
  slug: "northwind-logistics",
  role: "owner",
});

const CONTOSO = organization({
  id: "bbbbbbbb-0000-0000-0000-000000000002",
  name: "Contoso Freight",
  slug: "contoso-freight",
  role: "member",
});

describe("OrganizationSwitcher", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the active workspace and role without a menu when there is only one", async () => {
    stubApi({
      "/api/auth/session": {
        body: authenticatedSession({ organizations: [NORTHWIND] }),
      },
    });

    await renderWithSession(<OrganizationSwitcher />);

    expect(screen.getByText("Northwind Logistics")).toBeInTheDocument();
    expect(screen.getByText("owner")).toBeInTheDocument();
    // A menu with one option is a menu that does nothing.
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("lists every workspace the user belongs to", async () => {
    const user = userEvent.setup();
    stubApi({
      "/api/auth/session": {
        body: authenticatedSession({ organizations: [NORTHWIND, CONTOSO] }),
      },
    });

    await renderWithSession(<OrganizationSwitcher />);
    await user.click(screen.getByRole("button", { expanded: false }));

    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(2);
    expect(options[0]).toHaveAccessibleName(/Northwind Logistics/);
    expect(options[1]).toHaveAccessibleName(/Contoso Freight/);
  });

  it("switches on the server rather than filtering in the browser", async () => {
    // The decisive behaviour: switching must be a request, because the client
    // never holds another tenant's data to filter.
    const user = userEvent.setup();
    const { calls } = stubApi({
      "/api/auth/session": {
        body: authenticatedSession({ organizations: [NORTHWIND, CONTOSO] }),
      },
      "/api/auth/organization": {
        body: authenticatedSession({
          organizations: [NORTHWIND, CONTOSO],
          active_organization_id: CONTOSO.id,
        }),
      },
    });

    await renderWithSession(<OrganizationSwitcher />);
    await user.click(screen.getByRole("button", { expanded: false }));
    await user.click(screen.getByRole("option", { name: /Contoso Freight/ }));

    await waitFor(() => {
      const request = calls.find((call) =>
        call.url.endsWith("/api/auth/organization"),
      );
      expect(request).toBeDefined();
      expect(request?.method).toBe("POST");
      expect(request?.body).toEqual({ organization_id: CONTOSO.id });
    });
  });

  it("reflects the newly active workspace once the server confirms it", async () => {
    const user = userEvent.setup();
    stubApi({
      "/api/auth/session": {
        body: authenticatedSession({ organizations: [NORTHWIND, CONTOSO] }),
      },
      "/api/auth/organization": {
        body: authenticatedSession({
          organizations: [NORTHWIND, CONTOSO],
          active_organization_id: CONTOSO.id,
        }),
      },
    });

    await renderWithSession(<OrganizationSwitcher />);
    await user.click(screen.getByRole("button", { expanded: false }));
    await user.click(screen.getByRole("option", { name: /Contoso Freight/ }));

    await waitFor(() => {
      expect(screen.getByRole("button")).toHaveTextContent("Contoso Freight");
    });
  });

  it("reports a failed switch instead of appearing to have switched", async () => {
    const user = userEvent.setup();
    stubApi({
      "/api/auth/session": {
        body: authenticatedSession({ organizations: [NORTHWIND, CONTOSO] }),
      },
      "/api/auth/organization": {
        status: 403,
        body: {
          error: {
            code: "FORBIDDEN",
            message: "You are not a member of that organization.",
          },
        },
      },
    });

    await renderWithSession(<OrganizationSwitcher />);
    await user.click(screen.getByRole("button", { expanded: false }));
    await user.click(screen.getByRole("option", { name: /Contoso Freight/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not switch workspace.",
    );
    // Still showing the workspace the session is really in.
    expect(screen.getByRole("button", { expanded: true })).toHaveTextContent(
      "Northwind Logistics",
    );
  });
});

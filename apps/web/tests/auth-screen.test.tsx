import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthScreen } from "@/components/auth/auth-screen";

import {
  ANONYMOUS,
  authenticatedSession,
  renderWithSession,
  stubApi,
} from "./helpers";

describe("AuthScreen", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("signs in with the submitted credentials", async () => {
    const user = userEvent.setup();
    const { calls } = stubApi({
      "/api/auth/session": { body: ANONYMOUS },
      "/api/auth/login": { body: authenticatedSession() },
    });

    await renderWithSession(<AuthScreen />);

    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      const login = calls.find((call) => call.url.endsWith("/api/auth/login"));
      expect(login).toBeDefined();
      expect(login?.method).toBe("POST");
      expect(login?.body).toEqual({
        email: "ada@example.com",
        password: "correct-horse-battery",
      });
    });
  });

  it("shows the API's message when credentials are refused", async () => {
    const user = userEvent.setup();
    stubApi({
      "/api/auth/session": { body: ANONYMOUS },
      "/api/auth/login": {
        status: 401,
        body: {
          error: {
            code: "UNAUTHENTICATED",
            message: "Invalid email or password.",
          },
        },
      },
    });

    await renderWithSession(<AuthScreen />);

    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong-password-here");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid email or password.",
    );
  });

  it("never displays the submitted password after a failure", async () => {
    // The password field keeps its value so the person can correct it, but no
    // error message may quote it back.
    const user = userEvent.setup();
    stubApi({
      "/api/auth/session": { body: ANONYMOUS },
      "/api/auth/login": {
        status: 401,
        body: {
          error: {
            code: "UNAUTHENTICATED",
            message: "Invalid email or password.",
          },
        },
      },
    });

    await renderWithSession(<AuthScreen />);

    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "unique-secret-42");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).not.toContain("unique-secret-42");
  });

  it("maps field-level validation errors onto their fields", async () => {
    const user = userEvent.setup();
    stubApi({
      "/api/auth/session": { body: ANONYMOUS },
      "/api/auth/register": {
        status: 422,
        body: {
          error: {
            code: "VALIDATION_ERROR",
            message: "Request validation failed",
            details: [
              {
                loc: ["body", "email"],
                msg: "value is not a valid email address",
                type: "value_error",
              },
            ],
          },
        },
      },
    });

    await renderWithSession(<AuthScreen />);
    await user.click(screen.getByRole("button", { name: "Create one" }));

    await user.type(screen.getByLabelText("Your name"), "Ada Lovelace");
    await user.type(
      screen.getByLabelText("Workspace name"),
      "Analytical Engines",
    );
    await user.type(screen.getByLabelText("Email"), "not-an-email@x.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.click(screen.getByRole("button", { name: "Create workspace" }));

    await waitFor(() => {
      expect(
        screen.getByText("value is not a valid email address"),
      ).toBeInTheDocument();
    });
    expect(screen.getByLabelText("Email")).toHaveAttribute(
      "aria-invalid",
      "true",
    );
  });

  it("checks the password length before calling the API", async () => {
    const user = userEvent.setup();
    const { calls } = stubApi({ "/api/auth/session": { body: ANONYMOUS } });

    await renderWithSession(<AuthScreen />);
    await user.click(screen.getByRole("button", { name: "Create one" }));

    await user.type(screen.getByLabelText("Your name"), "Ada Lovelace");
    await user.type(
      screen.getByLabelText("Workspace name"),
      "Analytical Engines",
    );
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "tiny");
    await user.click(screen.getByRole("button", { name: "Create workspace" }));

    expect(
      await screen.findByText("Use at least 12 characters."),
    ).toBeInTheDocument();
    expect(calls.some((call) => call.url.endsWith("/api/auth/register"))).toBe(
      false,
    );
  });

  it("switches between signing in and creating a workspace", async () => {
    const user = userEvent.setup();
    stubApi({ "/api/auth/session": { body: ANONYMOUS } });

    await renderWithSession(<AuthScreen />);

    expect(screen.queryByLabelText("Workspace name")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Create one" }));
    expect(screen.getByLabelText("Workspace name")).toBeInTheDocument();
    expect(screen.getByLabelText("Your name")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.queryByLabelText("Workspace name")).not.toBeInTheDocument();
  });

  it("explains that the session ended when asked to", async () => {
    stubApi({ "/api/auth/session": { body: ANONYMOUS } });

    await renderWithSession(<AuthScreen expired />);

    expect(screen.getByRole("status")).toHaveTextContent("Your session ended");
  });

  it("labels every field for assistive technology", async () => {
    const user = userEvent.setup();
    stubApi({ "/api/auth/session": { body: ANONYMOUS } });

    await renderWithSession(<AuthScreen />);

    expect(screen.getByLabelText("Email")).toHaveAttribute("type", "email");
    expect(screen.getByLabelText("Password")).toHaveAttribute(
      "type",
      "password",
    );

    await user.click(screen.getByRole("button", { name: "Create one" }));
    expect(screen.getByLabelText("Password")).toHaveAttribute(
      "autocomplete",
      "new-password",
    );
  });
});

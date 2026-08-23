import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthScreen } from "@/components/auth/auth-screen";
import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";

import { ANONYMOUS, renderWithSession, stubApi } from "./helpers";

const SESSION = { "/api/auth/session": { body: ANONYMOUS } };
const VAGUE =
  "If that address belongs to an account, a reset link is on its way. The link expires in one hour.";

describe("Forgot password", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is reachable from the sign-in screen", async () => {
    const user = userEvent.setup({ delay: null });
    stubApi(SESSION);

    await renderWithSession(<AuthScreen />);

    await user.click(
      await screen.findByRole("button", { name: "Forgot your password?" }),
    );

    expect(
      await screen.findByRole("button", { name: "Send reset link" }),
    ).toBeInTheDocument();
  });

  it("is offered only when signing in, not when signing up", async () => {
    // Nobody creating an account has a password to forget.
    const user = userEvent.setup({ delay: null });
    stubApi(SESSION);

    await renderWithSession(<AuthScreen />);
    await user.click(screen.getByRole("button", { name: "Create one" }));

    expect(
      screen.queryByRole("button", { name: "Forgot your password?" }),
    ).not.toBeInTheDocument();
  });

  it("gives the same non-committal answer for any address", async () => {
    // The API refuses to say whether an account exists; the form must not
    // undo that at the last step by reporting success differently.
    const user = userEvent.setup({ delay: null });
    stubApi({
      ...SESSION,
      "/api/auth/forgot-password": { status: 202, body: { message: VAGUE } },
    });

    await renderWithSession(<ForgotPasswordForm onBack={() => {}} />);

    await user.type(
      await screen.findByLabelText("Email"),
      "nobody@example.com",
    );
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      /If that address belongs to an account/,
    );
  });

  it("does not claim the message has arrived", async () => {
    // Where no mail sender is configured the link goes to the server log.
    const user = userEvent.setup({ delay: null });
    stubApi({
      ...SESSION,
      "/api/auth/forgot-password": { status: 202, body: { message: VAGUE } },
    });

    await renderWithSession(<ForgotPasswordForm onBack={() => {}} />);
    await user.type(
      await screen.findByLabelText("Email"),
      "someone@example.com",
    );
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    const status = await screen.findByRole("status");
    expect(status.textContent).not.toMatch(/we sent|has been sent|delivered/i);
  });

  it("sends the address to the API", async () => {
    const user = userEvent.setup({ delay: null });
    const { calls } = stubApi({
      ...SESSION,
      "/api/auth/forgot-password": { status: 202, body: { message: VAGUE } },
    });

    await renderWithSession(<ForgotPasswordForm onBack={() => {}} />);
    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    await screen.findByRole("status");
    const call = calls.find((c) => c.url.includes("/api/auth/forgot-password"));
    expect(call?.body).toEqual({ email: "ada@example.com" });
  });

  it("surfaces a rate limit rather than pretending it worked", async () => {
    const user = userEvent.setup({ delay: null });
    stubApi({
      ...SESSION,
      "/api/auth/forgot-password": {
        status: 429,
        body: {
          error: {
            code: "RATE_LIMITED",
            message:
              "Too many reset requests for that address. Try again later.",
          },
        },
      },
    });

    await renderWithSession(<ForgotPasswordForm onBack={() => {}} />);
    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset link" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Too many reset requests/,
    );
  });
});

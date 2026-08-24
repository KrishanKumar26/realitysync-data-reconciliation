"""Delivering a reset link.

There is no mail provider configured in this deployment, and pretending
otherwise was not an option: an interface that says "check your inbox" when
nothing was sent is the same class of lie this product exists to argue
against.

So delivery is a seam. The default writes the link to the server log, which is
genuinely usable for a self-hosted or single-operator deployment — the person
who runs it can read it out of the log — and is honest about what it did.
Configure a real sender and the same flow starts emailing instead; nothing
else changes.

The **link is never logged at INFO alongside ordinary traffic** by accident:
it goes out at WARNING with an explicit event name, so it is obvious in a log
that this deployment is delivering reset links this way.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ResetLinkSender(Protocol):
    """How a reset link reaches a person."""

    async def send(self, *, email: str, link: str) -> None: ...

    @property
    def describes_itself_as_delivered(self) -> bool:
        """Whether this sender actually puts the link in front of the user.

        The API tells the caller the same thing either way — saying otherwise
        would leak whether the address exists — but an operator checking
        /api/system/status deserves to know that resets are only reaching the
        log.
        """
        ...


class LogOnlyResetLinkSender:
    """Writes the link to the server log. The default."""

    @property
    def describes_itself_as_delivered(self) -> bool:
        return False

    async def send(self, *, email: str, link: str) -> None:
        logger.warning(
            "password_reset.link_not_emailed",
            recipient=email,
            link=link,
            detail=(
                "No mail sender is configured, so this link was written here "
                "instead of being emailed. Configure one to deliver it."
            ),
        )


class ResendResetLinkSender:
    """Delivers through Resend's HTTP API.

    Chosen over SMTP because it needs one credential and no long-lived
    connection, which suits a container that may be asleep between requests.

    A failure here is logged and swallowed. That is deliberate and it is the
    uncomfortable choice: the caller's response must be identical whether or
    not the address exists, so it cannot also depend on whether the send
    worked — a 500 on delivery failure would say "this address is real" just
    as loudly as an error message would. The operator finds out from the log,
    which is where a delivery problem belongs.
    """

    #: Short. This runs inside a request, and a mail API having a bad day must
    #: not hold the reset endpoint open.
    TIMEOUT_SECONDS = 8.0

    def __init__(self, *, api_key: str, sender: str) -> None:
        self._api_key = api_key
        self._sender = sender

    @property
    def describes_itself_as_delivered(self) -> bool:
        return True

    async def send(self, *, email: str, link: str) -> None:
        payload = {
            "from": self._sender,
            "to": [email],
            "subject": "Reset your RealitySync password",
            "text": (
                "Someone asked to reset the password for this RealitySync "
                "account.\n\n"
                f"{link}\n\n"
                "The link works once and expires in one hour. If this was not "
                "you, nothing has changed and you can ignore this message."
            ),
        }

        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT_SECONDS) as client:
                response = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
            response.raise_for_status()
        except Exception as exc:
            # The link is *not* logged here. Falling back to the log on failure
            # would quietly turn a misconfigured mail setup into a deployment
            # that publishes reset links, which is worse than not sending.
            logger.error(
                "password_reset.send_failed",
                error_type=type(exc).__name__,
                detail="The reset link was not delivered. Check the mail configuration.",
            )
            return

        logger.info("password_reset.link_sent")


def build_reset_link_sender(settings: Settings) -> ResetLinkSender:
    """The sender these settings describe."""
    if settings.mail_configured:
        return ResendResetLinkSender(api_key=settings.resend_api_key, sender=settings.mail_from)
    return LogOnlyResetLinkSender()


_sender: ResetLinkSender = LogOnlyResetLinkSender()


def get_reset_link_sender() -> ResetLinkSender:
    return _sender


def set_reset_link_sender(sender: ResetLinkSender) -> None:
    """Swap the sender. Used by tests and by a future mail integration."""
    global _sender
    _sender = sender

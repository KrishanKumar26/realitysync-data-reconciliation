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


_sender: ResetLinkSender = LogOnlyResetLinkSender()


def get_reset_link_sender() -> ResetLinkSender:
    return _sender


def set_reset_link_sender(sender: ResetLinkSender) -> None:
    """Swap the sender. Used by tests and by a future mail integration."""
    global _sender
    _sender = sender

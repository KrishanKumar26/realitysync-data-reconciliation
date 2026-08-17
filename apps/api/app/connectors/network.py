"""Where a connector is allowed to connect.

A data source's host is supplied by whoever configures it. Without a
restriction, that turns every connector into a request forger: a tenant points
a "data source" at an internal address and RealitySync connects on their
behalf, from inside the deployment's network, using the deployment's identity.

Phase 3 recorded "private-network databases are outside MVP" as *scope*. Scope
is not a control. Phase 11 verified the gap was real by pointing a source at
several internal addresses from an ordinary tenant account, and the connection
test's own error codes formed a working port scanner:

    169.254.169.254:80   timeout       host exists, filtered
    127.0.0.1:6379       unreachable   nothing listening on that port
    postgres:5432        tls_failed    a PostgreSQL is running here

``unreachable`` against ``tls_failed`` is the whole scan: one says the port is
closed, the other says a database answered. That is enough to map a private
network from a signup form. And it gets worse than mapping — the application's
own database is reachable by hostname from inside the deployment, so a tenant
who guesses its credentials reads every other tenant's data through a feature
that is working exactly as designed.

The rule here: **resolve the host, and refuse anything that is not a public
address.** Resolution matters. Checking only IP literals would be trivially
bypassed by a hostname with an ``A`` record pointing at ``127.0.0.1``.

Known limitation, stated rather than papered over: this is checked at
configuration time and again at connect time, but the address could change
between the check and the connection — classic DNS rebinding. Closing that
needs the resolved address pinned and handed to the driver instead of the
hostname, which neither driver exposes cleanly. Two checks raise the cost
substantially; they do not eliminate it.
"""

from __future__ import annotations

import ipaddress
import socket

from app.connectors.types import ConnectorError, ConnectorErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)


def _is_public(address: str) -> bool:
    """Whether an IP address is routable on the public internet.

    Everything else is refused: loopback reaches the deployment itself,
    link-local reaches cloud metadata services, and the private ranges reach
    whatever else shares the network.
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False

    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
    )


def resolve_host(host: str) -> list[str]:
    """Every address `host` resolves to.

    All of them are checked, not just the first. A hostname with one public and
    one private address would otherwise pass while still being usable to reach
    the private one.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # Unresolvable is not a policy failure. Let the connection attempt
        # produce the ordinary "host could not be resolved" error, which is
        # what the operator needs to see.
        return []
    return sorted({str(info[4][0]) for info in infos})


def assert_host_is_permitted(host: str, *, allow_private: bool) -> None:
    """Refuse a host that resolves to a non-public address.

    ``allow_private`` exists for self-hosted deployments whose databases
    genuinely are on a private network, and for local development, where every
    Docker service address is private by definition. It defaults to off, so the
    safe posture is the one you get without deciding anything.
    """
    if allow_private:
        return

    addresses = resolve_host(host)
    if not addresses:
        return

    disallowed = [address for address in addresses if not _is_public(address)]
    if not disallowed:
        return

    # The resolved address stays out of the message. It is the answer to the
    # question the attacker is asking, and repeating it back would preserve
    # exactly the oracle this check exists to close.
    logger.warning(
        "connector.host_refused",
        host=host,
        resolved_count=len(addresses),
        disallowed_count=len(disallowed),
    )
    raise ConnectorError(
        ConnectorErrorCode.INVALID_CONFIGURATION,
        "That host is not permitted. RealitySync connects only to publicly "
        "routable database endpoints.",
        detail=f"{host} resolved to {len(disallowed)} non-public address(es)",
        remediation=(
            "Use a publicly reachable endpoint. Databases on a private network, "
            "loopback or a cloud metadata address cannot be reached by this "
            "deployment."
        ),
    )


def resolve_connect_address(host: str, *, allow_private: bool) -> str | None:
    """The single address a connector should dial, after the policy check.

    Returns ``None`` when the caller should let the driver resolve the name
    itself: either private hosts are allowed (local development, where Docker
    service names only resolve inside the container) or the name does not
    resolve here, in which case the driver's own "could not be resolved" error
    is the one the operator needs to see.

    **IPv4 is preferred.** Managed database providers publish both A and AAAA
    records, and several hosting platforms — Render among them — have no
    outbound IPv6 route at all. There the driver picks the AAAA record and the
    connection fails with "network is unreachable" against a database that is
    perfectly reachable over IPv4. Preferring A costs nothing where IPv6 works
    and is the difference between working and not where it does not.

    Pinning the address also closes the DNS-rebinding gap: the address checked
    by the policy is now the address actually dialled, so a name that resolves
    to a public address during the check and a private one a moment later no
    longer reaches the private one.
    """
    assert_host_is_permitted(host, allow_private=allow_private)

    if allow_private:
        return None

    addresses = resolve_host(host)
    if not addresses:
        return None

    for address in addresses:
        if isinstance(ipaddress.ip_address(address), ipaddress.IPv4Address):
            return address
    return addresses[0]

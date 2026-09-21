"""Local-first, cloud-fallback orchestration, with a circuit breaker.

The device's local TCP listener leaks and stops accepting connections after
a few days of uptime -- a firmware defect with no known client-side fix,
recoverable only by power-cycling it. Home Assistant's stock behaviour in
that situation is to mark the entity unavailable until someone notices.
CozyLife's cloud relay keeps working throughout, which is why the phone app
never notices.

This class sits between the entity and the two transports. It prefers the
local one, falls back to the relay when local is unreachable, and returns
to local as soon as it recovers.

The breaker is what makes local-first affordable. Without it, a listener
that has been dead for three days means every poll and every button press
first waits out the local timeout -- seconds of lag on a light switch, for
days. After a few consecutive failures local is skipped entirely and
requests go straight to the relay, with an occasional probe to notice a
power-cycle. It also collapses connection churn while local is down, which
matters if the leak is per-connection.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from .errors import TransportError

#: Consecutive local failures before local is skipped. More than one, so a
#: single dropped poll is not mistaken for the firmware fault.
DEFAULT_FAILURE_THRESHOLD = 3

#: How long to leave local alone before probing it again. Nothing announces
#: a power-cycle, so periodic probing is the only way to notice one.
DEFAULT_RECOVERY_INTERVAL = 600.0

_LOGGER = logging.getLogger(__name__)


class Transport(Protocol):
    """What both transports expose. Nothing else is required of them."""

    name: str

    def query(self, attrs: list[int] | None = None) -> dict[str, Any]: ...

    def control(self, payload: dict[Any, int]) -> bool: ...


class FallbackClient:
    """Serve each request over the local transport, or the cloud if it must.

    ``cloud`` may be ``None``, which makes this local-only -- the state the
    integration is in before cloud credentials have been supplied.
    """

    def __init__(
        self,
        local: Transport,
        cloud: Transport | None,
        *,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        recovery_interval: float = DEFAULT_RECOVERY_INTERVAL,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._local = local
        self._cloud = cloud
        self._failure_threshold = failure_threshold
        self._recovery_interval = recovery_interval
        self._monotonic = monotonic

        self._active_path: str | None = None
        self._consecutive_failures = 0
        self._circuit_opened_at: float | None = None

    @property
    def active_path(self) -> str | None:
        """Which transport served the most recent request, for diagnostics."""

        return self._active_path

    @property
    def has_cloud_fallback(self) -> bool:
        return self._cloud is not None

    @property
    def local_circuit_open(self) -> bool:
        """Whether local is currently being skipped."""

        return self._circuit_opened_at is not None

    def close(self) -> None:
        """Release anything the transports hold.

        The local transport keeps a socket open between calls, and leaving
        it dangling across a config entry reload would leak one every time
        -- on a device whose whole problem is thought to be exactly that.
        """

        for transport in (self._local, self._cloud):
            closer = getattr(transport, "close", None)
            if callable(closer):
                closer()

    def query(self, attrs: list[int] | None = None) -> dict[str, Any]:
        """Read datapoints over whichever path is working."""

        return self._attempt(lambda transport: transport.query(attrs))

    def control(self, payload: dict[Any, int]) -> bool:
        """Write datapoints over whichever path is working.

        A device that answers and refuses the command returns ``False`` and
        is *not* retried on the other path: it was reached, the cloud cannot
        do better, and a retry would drive the hardware a second time.
        """

        return self._attempt(lambda transport: transport.control(payload))

    def _attempt(self, call):
        if not self._should_try_local():
            # Straight to the relay: local is known down and trying it would
            # only add its timeout to every request.
            return self._via_cloud(call, local_error=None)

        try:
            result = call(self._local)
        except TransportError as local_error:
            self._record_local_failure()
            if self._cloud is None:
                self._active_path = None
                raise
            _LOGGER.debug("Local transport failed, trying cloud: %s", local_error)
            return self._via_cloud(call, local_error=local_error)

        self._record_local_success()
        self._note_path(self._local.name)
        return result

    def _via_cloud(self, call, *, local_error: TransportError | None):
        assert self._cloud is not None

        try:
            result = call(self._cloud)
        except TransportError as cloud_error:
            self._active_path = None
            if local_error is None:
                raise
            raise TransportError(
                f"both paths failed -- local: {local_error}; cloud: {cloud_error}"
            ) from cloud_error

        self._note_path(self._cloud.name)
        return result

    def _should_try_local(self) -> bool:
        """Whether this request should attempt the local transport."""

        # Without a cloud path there is nothing else to serve the request,
        # so local is always worth trying however often it has failed.
        if self._cloud is None or self._circuit_opened_at is None:
            return True

        if self._monotonic() - self._circuit_opened_at < self._recovery_interval:
            return False

        # Time for a single probe. Reset the clock first, so that a probe
        # which also fails waits out another full interval rather than
        # letting every following request probe again.
        self._circuit_opened_at = self._monotonic()
        _LOGGER.debug("Probing the local path to see whether it has recovered")
        return True

    def _record_local_failure(self) -> None:
        self._consecutive_failures += 1
        if (
            self._circuit_opened_at is None
            and self._cloud is not None
            and self._consecutive_failures >= self._failure_threshold
        ):
            self._circuit_opened_at = self._monotonic()
            _LOGGER.info(
                "CozyLife local path has failed %d times in a row; serving from "
                "the cloud and re-checking every %.0fs",
                self._consecutive_failures,
                self._recovery_interval,
            )

    def _record_local_success(self) -> None:
        # Failures only mean something consecutively; one success clears the
        # tally so intermittent blips never accumulate into a trip.
        self._consecutive_failures = 0
        if self._circuit_opened_at is not None:
            self._circuit_opened_at = None
            _LOGGER.info("CozyLife local path is answering again")

    def _note_path(self, path: str) -> None:
        """Record the serving path, announcing only genuine transitions.

        Polling runs all day; logging every request at INFO would bury the
        one event that matters. A transition is exactly the thing worth
        seeing in ``ha core logs``.
        """

        if path == self._active_path:
            _LOGGER.debug("Served over the %s path", path)
            return

        if self._active_path is None:
            _LOGGER.info("CozyLife requests are being served over the %s path", path)
        else:
            _LOGGER.info(
                "CozyLife switched from the %s path to the %s path",
                self._active_path,
                path,
            )
        self._active_path = path

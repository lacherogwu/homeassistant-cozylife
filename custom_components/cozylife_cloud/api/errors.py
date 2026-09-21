"""Errors shared by both transports."""

from __future__ import annotations


class CozyLifeError(Exception):
    """Base class for every error this package raises."""


class TransportError(CozyLifeError):
    """A transport could not complete an exchange with the device.

    Raised for "couldn't reach it" -- refused connections, timeouts, a
    listener that accepts and then says nothing. Deliberately distinct from
    a device that answered and refused the command, which is an ordinary
    ``False`` return: only the former is worth failing over to the other
    transport for.
    """


class AuthError(CozyLifeError):
    """CozyLife's cloud rejected the credentials or the session token."""

"""Transport-agnostic CozyLife protocol code.

Nothing in this package imports Home Assistant. That keeps the wire
protocol testable on its own, without a HA test harness, and keeps the
HA-facing modules thin enough to read in one sitting.
"""

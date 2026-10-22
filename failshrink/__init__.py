"""Dependency-aware reduction of ordered JSON traces."""

from .core import Trace, Verdict, reduce_trace

__all__ = ["Trace", "Verdict", "reduce_trace"]
__version__ = "0.1.0"

"""Typed composition root and application installation boundary."""

from .application import create_application
from .container import AppContainer, LegacyCompatibilityBindings

__all__ = ["AppContainer", "LegacyCompatibilityBindings", "create_application"]

"""Decoder package: importing this subpackage registers all decoders with
``protocol.registry.DEFAULT_REGISTRY``. Import this before calling
``DEFAULT_REGISTRY.decode()``.
"""
from protocol.decoders import electrical, engine, identity, navigation, tank  # noqa: F401

__all__ = ["engine", "tank", "electrical", "navigation", "identity"]

"""Capability layer — domain logic, transport-agnostic.

Each module exposes one or more async functions that accept a Transport
and return a Result[T]. The MCP and CLI layers are thin wrappers that
build the Transport and forward to capability functions.
"""

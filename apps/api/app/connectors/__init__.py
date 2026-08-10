"""Data connectors.

`base` and `types` define the interface every source implements; `registry`
resolves a source kind to an implementation. Ingestion imports only those
three, never a concrete connector, which is what lets a new source type be
added without touching anything downstream.
"""

"""Local web interface for fra, started with ``fra ui``."""

from fra.web.server import RunManager, make_server, serve

__all__ = ["RunManager", "make_server", "serve"]

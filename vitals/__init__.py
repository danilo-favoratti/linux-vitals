"""Vitals - a real-time GTK4 system monitor with an embedded web console."""

from . import bootstrap

# Must run before anything touches gi.repository.
bootstrap.install_logging()
bootstrap.raise_nofile()
bootstrap.add_vendor_typelibs()

__version__ = "2.0.0"

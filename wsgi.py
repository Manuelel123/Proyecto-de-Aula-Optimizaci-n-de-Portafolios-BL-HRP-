"""WSGI entry point for production servers."""

from optimizacion_portafolios.app import create_app

app = create_app()

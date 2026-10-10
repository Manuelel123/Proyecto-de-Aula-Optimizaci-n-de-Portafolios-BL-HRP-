"""WSGI entry point for production servers."""

from optimizacion_portafolios.web import create_app

app = create_app()

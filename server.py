# Simple WSGI app for Render health checks
import os
from logger import LOGGER

def app(environ, start_response):
    """Simple WSGI app for health checks"""
    path = environ.get('PATH_INFO', '/')
    
    if path in ['/', '/health', '/ping']:
        status = '200 OK'
        response_headers = [('Content-Type', 'application/json')]
        start_response(status, response_headers)
        return [b'{"status": "ok", "message": "ChatBot is running"}']
    
    status = '404 Not Found'
    response_headers = [('Content-Type', 'text/plain')]
    start_response(status, response_headers)
    return [b'Not Found']

if __name__ == '__main__':
    from waitress import serve
    port = int(os.getenv('PORT', 5000))
    LOGGER(__name__).info(f"Starting HTTP server on port {port}")
    serve(app, host='0.0.0.0', port=port)

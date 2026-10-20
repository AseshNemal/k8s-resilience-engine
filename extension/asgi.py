"""ASGI entrypoint for the Docker Desktop extension backend.

Serves the unmodified research application and adds the extension-only cluster connection
routes and the error envelope. app/main.py is not changed; the extras are attached here.
"""

from app.main import app
from extension.cluster_connection import router
from extension.error_envelope import ErrorEnvelope

app.include_router(router)

# `application` is what uvicorn serves; `app` stays the plain FastAPI instance for tests.
application = ErrorEnvelope(app)

"""Lambda entrypoint: the Rewind API plus the built dashboard, one function.

The deploy script bundles this file at the zip root together with the
``rewind`` package, third-party deps, ``dashboard_dist/`` (the Vite build),
and ``certs/root.crt`` (the CockroachDB Cloud CA). REWIND_DATABASE_URL comes
from the function's environment and must reference that cert path.
"""

from mangum import Mangum

from fastapi.staticfiles import StaticFiles
from rewind.api import create_app

app = create_app()
# Mounted last so /api/* routes win; html=True serves index.html at "/".
app.mount("/", StaticFiles(directory="dashboard_dist", html=True), name="dashboard")

handler = Mangum(app)

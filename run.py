"""Development entry point.

Usage:
    python run.py            # initialise the database (if needed) and run
    python run.py init-db    # only initialise the database, then exit

For production deployments this file is intentionally simple; use a WSGI
server with ``wsgi.py`` and the systemd unit in ``deploy/`` instead.
"""

import sys

from server import create_app
from server.config import ConfigError
from server.db import init_db


def main():
    try:
        app = create_app()
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2

    database_path = app.config["DATABASE_PATH"]

    if len(sys.argv) > 1 and sys.argv[1] == "init-db":
        init_db(database_path, app.config["SQLITE_BUSY_TIMEOUT_MS"])
        print(f"Database initialised at: {database_path}")
        return 0

    # Initialising here is idempotent and keeps local demos simple.
    init_db(database_path, app.config["SQLITE_BUSY_TIMEOUT_MS"])
    print(f"Database ready at: {database_path}")
    print(f"Starting development server on http://{app.config['HOST']}:{app.config['PORT']}")
    print("WARNING: Flask's development server is not suitable for production.")
    app.run(
        host=app.config["HOST"],
        port=app.config["PORT"],
        debug=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

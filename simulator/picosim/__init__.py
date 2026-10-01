import os
from pathlib import Path

from flask import Flask

from .devices import DeviceManager


def create_app(server_url=None, data_dir=None, http=None):
    app = Flask(__name__)
    app.config['DEVICE_MANAGER'] = DeviceManager(
        data_dir or os.environ.get('SIM_DATA_DIR', Path(__file__).resolve().parent.parent / 'data'),
        server_url or os.environ.get('PICOBREW_SERVER_URL', 'http://localhost:8080'),
        http=http,
    )

    from .routes import bp
    app.register_blueprint(bp)
    return app

"""Starts the PicoBrew device simulator web UI.

Environment:
  PICOBREW_SERVER_URL  picobrew_pico server new devices talk to (default http://localhost:8080)
  SIM_DATA_DIR         where simulated device definitions are saved (default ./data)
  HOST / PORT          bind address (default 0.0.0.0:8090)
"""
import os

from picosim import create_app

app = create_app()

if __name__ == '__main__':
    app.run(host=os.environ.get('HOST', '0.0.0.0'), port=int(os.environ.get('PORT', 8090)), threaded=True)

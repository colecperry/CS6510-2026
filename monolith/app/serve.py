# Launches the app via hypercorn, with HTTP/2 upgrades disabled.
# Run as: uv run python -m app.serve
import asyncio

import h11
from hypercorn.asyncio import serve
from hypercorn.config import Config
from hypercorn.protocol import h11 as hypercorn_h11

from app.main import app


# The Java load client probes every request with "Upgrade: h2c". Hypercorn
# ignores that probe when the request has a body, but the client's bodyless
# GET /items at startup does trigger a real upgrade - after which the client
# sends everything over HTTP/2, which destabilises badly under sustained
# load (stream-limit errors, then mass timeouts). Plain HTTP/1.1 handles the
# same concurrency without trouble, so we refuse the upgrade entirely.
# Hypercorn exposes no config flag for this, hence the patch.
async def _refuse_h2c_upgrade(self, event: h11.Request) -> None:
    if event.method == b"PRI" and event.target == b"*" and event.http_version == b"2.0":
        raise hypercorn_h11.H2ProtocolAssumedError(
            b"PRI * HTTP/2.0\r\n\r\n" + self.connection.trailing_data[0]
        )


hypercorn_h11.H11Protocol._check_protocol = _refuse_h2c_upgrade

config = Config()
config.bind = ["0.0.0.0:8080"]

if __name__ == "__main__":
    asyncio.run(serve(app, config))

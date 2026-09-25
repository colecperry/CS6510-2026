# Starts the web server. Run with: uv run python -m app.serve
#
# Use this rather than launching hypercorn directly, because it disables
# HTTP/2 upgrades first. README.md explains why at length.
import asyncio

import h11
from hypercorn.asyncio import serve
from hypercorn.config import Config
from hypercorn.protocol import h11 as hypercorn_h11

from app.main import app


# The Java load client offers to upgrade every request to HTTP/2. If
# hypercorn accepts, the client switches protocol and then collapses under
# sustained load with stream-limit errors and timeouts. Plain HTTP/1.1
# handles the same concurrency without trouble, so we refuse the offer.
# Hypercorn has no config flag for this, hence patching the method.
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

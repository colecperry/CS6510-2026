# Starts the web server. Run with: uv run python -m app.serve
#
# Use this rather than launching hypercorn directly, because it disables HTTP/2 upgrades first. README.md explains why at length.
import asyncio

import h11
from hypercorn.asyncio import serve
from hypercorn.config import Config
from hypercorn.protocol import h11 as hypercorn_h11

from app.main import app


# The test client asks to switch to a newer version of HTTP on every request. Accepting makes it collapse under load, so we say no and stay on the older one, which handles the same traffic fine. There is no setting for this, so the check is replaced by hand below.
async def _refuse_h2c_upgrade(self, event: h11.Request) -> None:
    if event.method == b"PRI" and event.target == b"*" and event.http_version == b"2.0":
        raise hypercorn_h11.H2ProtocolAssumedError(
            b"PRI * HTTP/2.0\r\n\r\n" + self.connection.trailing_data[0]
        )


hypercorn_h11.H11Protocol._check_protocol = _refuse_h2c_upgrade   # the swap

config = Config()
config.bind = ["0.0.0.0:8080"]   # all network interfaces, port 8080

if __name__ == "__main__":
    asyncio.run(serve(app, config))

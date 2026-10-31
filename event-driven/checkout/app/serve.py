# Starts the checkout service. Run with: uv run --directory checkout python -m app.serve
#
# Each service has its own copy of this, differing only in the port. The HTTP-version patch below is carried from previous weeks and is not optional.
import asyncio

import h11
from hypercorn.asyncio import serve
from hypercorn.config import Config
from hypercorn.protocol import h11 as hypercorn_h11

from app.main import app

PORT = 8081


# The test client asks to switch to a newer version of HTTP on every request. Accepting makes the server collapse under load, so we say no and stay on the older one, which handles the same traffic fine. There is no setting for this, so the check is replaced by hand below.
async def _refuse_h2c_upgrade(self, event: h11.Request) -> None:
    if event.method == b"PRI" and event.target == b"*" and event.http_version == b"2.0":
        raise hypercorn_h11.H2ProtocolAssumedError(
            b"PRI * HTTP/2.0\r\n\r\n" + self.connection.trailing_data[0]
        )


hypercorn_h11.H11Protocol._check_protocol = _refuse_h2c_upgrade   # the swap

config = Config()
config.bind = [f"0.0.0.0:{PORT}"]

if __name__ == "__main__":
    asyncio.run(serve(app, config))

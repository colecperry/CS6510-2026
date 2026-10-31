# How every service reaches the message broker, and nothing about what they say to it.
#
# A broker is a separate process that holds messages: a service hands one over and stops waiting, and the broker keeps it until whoever wants it is running and ready.
# This file holds no message names and no payload shapes, for the same reason common/ holds no SQL. A shape both services import is a coupling, so each message belongs to the service that publishes it.
import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import aio_pika

logger = logging.getLogger(__name__)

# Every message in this system passes through one exchange. An exchange is the broker's sorting office: publishers hand messages to it with a label, and it copies each one into whichever queues asked for that label.
EXCHANGE = "selfcheckout"

# A quorum queue keeps its contents on several broker nodes and acknowledges a write only once a majority has it. On one node it still gives the stronger on-disk guarantees and the redelivery counting that the classic queue type does not.
QUEUE_ARGS = {"x-queue-type": "quorum"}

# How many messages the broker may hand one consumer before it acknowledges any of them. Low enough that a second copy of a service would be given real work rather than finding the queue already claimed, high enough that the consumer is never idle waiting for the next one to arrive.
DEFAULT_PREFETCH = 50


class Bus:
    """One service's connection to the broker. Built once at startup and closed at shutdown."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._channel: aio_pika.abc.AbstractChannel | None = None
        self._fast_channel: aio_pika.abc.AbstractChannel | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None
        self._fast_exchange: aio_pika.abc.AbstractExchange | None = None

    async def connect(self, prefetch: int = DEFAULT_PREFETCH) -> None:
        """Opens the connection and declares the shared exchange. Safe for every service to call, because declaring something that already exists does nothing."""
        # Robust rather than plain: this variant reconnects by itself when the broker restarts, and re-declares everything it had declared, which is the difference between an outage being survivable and every service needing a restart after one.
        self._connection = await aio_pika.connect_robust(self._url)

        # Two channels because the two message paths want opposite things. A channel in confirm mode makes every publish wait for the broker to say it has the message; a channel without it returns immediately and may lose one.
        self._channel = await self._connection.channel(publisher_confirms=True)
        self._fast_channel = await self._connection.channel(publisher_confirms=False)

        await self._channel.set_qos(prefetch_count=prefetch)

        # Durable and topic: durable so the exchange survives a broker restart, topic so a queue subscribes by naming the labels it wants rather than by being wired to a publisher.
        self._exchange = await self._channel.declare_exchange(
            EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        self._fast_exchange = await self._fast_channel.declare_exchange(
            EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )

    async def publish(self, routing_key: str, body: dict, persistent: bool = True) -> None:
        """Sends one message. With persistent set, returns only once the broker has written it to disk and confirmed it."""
        # Persistence is the caller's choice because the two message paths are worth different amounts: losing a sale costs money, losing a scan costs a ranking a little accuracy.
        if persistent:
            await self._exchange.publish(
                aio_pika.Message(
                    body=json.dumps(body).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    content_type="application/json",
                ),
                routing_key=routing_key,
            )
            return

        await self._fast_exchange.publish(
            aio_pika.Message(
                body=json.dumps(body).encode(),
                delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT,
                content_type="application/json",
            ),
            routing_key=routing_key,
        )

    async def consume(
        self,
        queue_name: str,
        routing_key: str,
        handler: Callable[[dict], Awaitable[None]],
    ) -> aio_pika.abc.AbstractQueue:
        """Declares a queue, binds it to the exchange and starts feeding the handler. Returns as soon as consumption has started."""
        # The queue is declared by whichever service wants the messages, not by the one that sends them, and it exists from that moment whether or not that service is running. That is what lets a stopped service come back to the work that arrived while it was down.
        queue = await self._channel.declare_queue(
            queue_name, durable=True, arguments=QUEUE_ARGS
        )
        await queue.bind(self._exchange, routing_key)

        async def _on_message(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            # Acknowledgement happens when this block exits cleanly, never before, and a raised exception returns the message to the queue instead. A crash part way through therefore redelivers the work rather than losing it.
            async with message.process():
                await handler(json.loads(message.body))

        await queue.consume(_on_message)
        return queue

    async def close(self) -> None:
        """Closes the connection. Call at shutdown."""
        if self._connection is not None:
            await self._connection.close()


async def connect_with_retry(
    url: str, prefetch: int = DEFAULT_PREFETCH, attempts: int = 30
) -> Bus:
    """Opens a Bus, waiting for the broker if it is still starting. Raises once the attempts run out."""
    # The broker takes several seconds longer to accept connections than Postgres does, so a service started alongside it would otherwise die before the broker was listening.
    bus = Bus(url)
    for attempt in range(attempts):
        try:
            await bus.connect(prefetch)
            return bus
        except Exception:
            if attempt == attempts - 1:
                raise
            logger.info("broker not ready, retrying")
            await asyncio.sleep(1)
    return bus

"""Local MQTT broker for development, no Docker needed.

    python dev/broker.py

TCP on 1883 (Python services) and WebSockets on 9001 (dashboard).
Anonymous access, localhost only. For the real demo use HiveMQ Cloud or EMQX.
"""

import asyncio
import logging

from amqtt.broker import Broker

CONFIG = {
    "listeners": {
        "default": {"type": "tcp", "bind": "127.0.0.1:1883"},
        "ws": {"type": "ws", "bind": "127.0.0.1:9001"},
    },
    "plugins": {
        "amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True},
    },
}


async def main() -> None:
    broker = Broker(CONFIG)
    await broker.start()
    print("Dev broker up: mqtt://localhost:1883  ws://localhost:9001")
    await asyncio.Event().wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())

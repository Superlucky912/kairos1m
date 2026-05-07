from __future__ import annotations

from typing import Any

import zmq
import zmq.asyncio


class ZMQPublisher:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.context = zmq.asyncio.Context.instance()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.bind(endpoint)

    async def send(self, payload: Any) -> None:
        await self.socket.send_json(payload)

    def close(self) -> None:
        self.socket.close(0)


class ZMQSubscriber:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.context = zmq.asyncio.Context.instance()
        self.socket = self.context.socket(zmq.SUB)
        self.socket.setsockopt_string(zmq.SUBSCRIBE, "")
        self.socket.connect(endpoint)

    async def recv(self) -> Any:
        return await self.socket.recv_json()

    def close(self) -> None:
        self.socket.close(0)



class ZMQReplyServer:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.context = zmq.asyncio.Context.instance()
        self.socket = self.context.socket(zmq.REP)
        self.socket.bind(endpoint)

    async def recv(self) -> Any:
        return await self.socket.recv_json()

    async def send(self, payload: Any) -> None:
        await self.socket.send_json(payload)

    def close(self) -> None:
        self.socket.close(0)


class ZMQRequestClient:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self.context = zmq.asyncio.Context.instance()
        self.socket = self.context.socket(zmq.REQ)
        self.socket.connect(endpoint)

    async def request(self, payload: Any) -> Any:
        await self.socket.send_json(payload)
        return await self.socket.recv_json()

    def close(self) -> None:
        self.socket.close(0)

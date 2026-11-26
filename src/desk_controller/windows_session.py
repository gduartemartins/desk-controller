"""A reusable Bluetooth connection owned by the Windows worker."""

import asyncio


class DeskSession:
    def __init__(self, factory):
        self.factory = factory
        self.desk = None
        self.address = None

    async def get(self, address):
        if self.desk is not None and (self.address != address or not self.desk.connected):
            await self.close()
        if self.desk is None:
            desk = self.factory(address)
            try:
                await desk.connect()
            except BaseException:
                await asyncio.shield(desk.disconnect())
                raise
            self.desk = desk
            self.address = address
        return self.desk

    async def close(self):
        if self.desk is not None:
            desk = self.desk
            self.desk = None
            self.address = None
            await desk.disconnect()

    async def run(self, address, operation):
        desk = await self.get(address)
        try:
            return await operation(desk)
        except asyncio.CancelledError:
            await asyncio.shield(self.close())
            raise
        except Exception:
            await self.close()
            raise

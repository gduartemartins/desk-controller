"""Serialize desk operations on an asyncio thread without blocking the GUI."""

import asyncio
import concurrent.futures
import threading


class BusyError(Exception):
    pass


class DeskWorker:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._tasks = set()
        self._lock = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self._lock = asyncio.Lock()
        self.loop.run_forever()
        self.loop.close()

    def submit(self, operation, *, takeover=False):
        result = concurrent.futures.Future()

        def start():
            pending = [task for task in self._tasks if not task.done()]
            if pending and not takeover:
                result.set_exception(BusyError("A desk operation is already in progress."))
                return
            if takeover:
                for task in pending:
                    if not task.cancelling():
                        task.cancel()

            async def run():
                async with self._lock:
                    return await operation()

            task = self.loop.create_task(run())
            self._tasks.add(task)

            def finish(done):
                self._tasks.discard(done)
                if done.cancelled():
                    result.cancel()
                else:
                    error = done.exception()
                    if error is not None:
                        result.set_exception(error)
                    else:
                        result.set_result(done.result())

            task.add_done_callback(finish)

        self.loop.call_soon_threadsafe(start)
        return result

    def shutdown(self, cleanup=None):
        async def finish():
            pending = [task for task in self._tasks if not task.done()]
            for task in pending:
                if not task.cancelling():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if cleanup is not None:
                await cleanup()

        result = asyncio.run_coroutine_threadsafe(finish(), self.loop)
        result.add_done_callback(lambda _: self.loop.call_soon_threadsafe(self.loop.stop))
        return result

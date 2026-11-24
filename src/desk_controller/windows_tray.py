"""Windows notification-area icon; callbacks are dispatched to the UI thread."""

import threading


class TrayIcon:
    def __init__(self, dispatch):
        import pystray
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((2, 2, 61, 61), radius=14, fill="#102b37")
        draw.rounded_rectangle((10, 19, 54, 25), radius=2, fill="#68e9df")
        draw.rectangle((16, 25, 20, 45), fill="#68e9df")
        draw.rectangle((44, 25, 48, 45), fill="#68e9df")
        draw.line((11, 46, 25, 46), fill="#68e9df", width=3)
        draw.line((39, 46, 53, 46), fill="#68e9df", width=3)
        menu = pystray.Menu(
            pystray.MenuItem("Open Desk Controller", lambda: dispatch("show"), default=True),
            pystray.MenuItem("Stop desk", lambda: dispatch("stop")),
            pystray.MenuItem("Quit", lambda: dispatch("quit")),
        )
        self.icon = pystray.Icon("desk-controller", image, "Desk Controller", menu)
        ready = threading.Event()

        def setup(icon):
            icon.visible = True
            ready.set()

        self.icon.run_detached(setup)
        if not ready.wait(5):
            self.icon.stop()
            raise RuntimeError("The notification-area icon could not be started.")

    def stop(self):
        self.icon.stop()

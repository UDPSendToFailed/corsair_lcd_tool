import atexit
import math
import time
import logging
from PyQt6.QtCore import QThread, pyqtSignal, QTimer, QObject
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QMessageBox
from openrgb import OpenRGBClient
from openrgb.utils import RGBColor


class LEDController(QThread):
    captureSignal = pyqtSignal()

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.enabled = True
        self.client = self.connect_to_openrgb()
        if not self.client:
            QMessageBox.warning(None, "OpenRGB Connection Error",
                                "OpenRGB is required for LED control. "
                                "Please ensure OpenRGB is running and SDK Server is enabled.")
            self.enabled = False
            return
        self.last_rgb_colors = [RGBColor(0, 0, 0)] * 24
        self.worker = LEDWorker(self.client, self.last_rgb_colors, self)

        self.started.connect(self.worker.set_colors)

        QTimer.singleShot(0, self.start_timer)

        atexit.register(self.stop_led)

    def connect_to_openrgb(self):
        for attempt in range(2):
            try:
                return OpenRGBClient()
            except Exception as e:
                if attempt == 0:
                    logging.warning("Retrying after 5 seconds...")
                time.sleep(5)
        return None

    def start_timer(self):
        self.update_led_timer = QTimer()
        self.update_led_timer.timeout.connect(self.analyze_and_set_colors)
        self.update_led_timer.start(100)

    def analyze_and_set_colors(self, width=479, height=479, smoothing_factor=0.25, saturation_factor=1.25):
        if not self.enabled:
            return

        try:
            image = self.main.capture_container()
            radius = min(width, height) // 2

            for i in range(24):
                angle = 2 * math.pi * (23 - i) / 24
                x = width - int(width / 2 + radius * math.cos(angle))
                y = int(height / 2 + radius * math.sin(angle))

                color = QColor(image.pixel(x, y))
                color = color.toHsv()


                color.setHsv(color.hue(), min(255, int(color.saturation() * saturation_factor)), color.value())

                rgb_color = RGBColor(color.red(), color.green(), color.blue())


                self.last_rgb_colors[i] = RGBColor(
                    int(self.last_rgb_colors[i].red * (
                            1 - smoothing_factor) + rgb_color.red * smoothing_factor),
                    int(self.last_rgb_colors[i].green * (
                            1 - smoothing_factor) + rgb_color.green * smoothing_factor),
                    int(self.last_rgb_colors[i].blue * (
                            1 - smoothing_factor) + rgb_color.blue * smoothing_factor)
                )

            self.started.emit()
        except Exception as e:
            logging.error(f"Error updating LED: {e}")

    def stop_led(self):
        self.enabled = False
        self.update_led_timer.stop()
        self.worker.turn_off_leds()


class LEDWorker(QObject):
    colorReady = pyqtSignal(object)

    def __init__(self, client, last_rgb_colors, controller):
        super().__init__()
        self.client = client
        self.last_rgb_colors = last_rgb_colors
        self.controller = controller

    def set_colors(self):
        if not self.controller.enabled:
            return
        try:
            for device in self.client.ee_devices:
                if device.name == "Corsair Commander Core":
                    pump_zone = next(zone for zone in device.zones if zone.name == "Pump")
                    for i, rgb_color in enumerate(self.last_rgb_colors):
                        pump_zone.leds[i].set_color(rgb_color, fast=True)
        except Exception as e:
            logging.error(f"Error setting colors: {e}")

    def turn_off_leds(self):
        time.sleep(0.1)
        for device in self.client.ee_devices:
            if device.name == "Corsair Commander Core":
                device.clear()

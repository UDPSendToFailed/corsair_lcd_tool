import logging
import os
import platform
import sys
from dataclasses import dataclass
import cv2
import hid
import numpy as np
import yaml
from PyQt6.QtCore import Qt, QTimer, QPointF, pyqtSignal, QThread, pyqtSlot, QRectF
from PyQt6.QtGui import QPixmap, QPainter, QMovie, QIcon, QTransform, QImage, QAction, QPalette, QColor
from PyQt6.QtWidgets import QApplication, QMainWindow, QPushButton, QFileDialog, QSlider, QWidget, QGraphicsScene, \
    QGraphicsView, QGraphicsPixmapItem, QSystemTrayIcon, QMenu, QStyleFactory, QGraphicsItem, QCheckBox

try:
    from led_controller_openrgb import LEDController
    led_controller_enabled = True
except ImportError:
    led_controller_enabled = False

if platform.system() == 'Windows':
    import winshell
elif platform.system() == 'Linux':
    import subprocess

# Set up logging
logging.basicConfig(level=logging.DEBUG)

VID = 0x1b1c  # Corsair
PID = 0x0c39  # Corsair LCD Cap for Elite Capellix coolers


@dataclass
class CorsairCommand:
    opcode: int  # 0x02
    unknown1: int  # 0x05
    unknown2: int  # 0x40
    is_end: bool  # 0x00 or 0x01
    part_num: int  # 0x0000 - 0xffff, little endian
    datalen: int  # 0x0000 - 0xffff, little endian
    data: bytes  # datalen bytes + padding up to packet size

    HEADER_SIZE = 8

    def to_bytes(self):
        return bytes([
            self.opcode,
            self.unknown1,
            self.unknown2,
            0x01 if self.is_end else 0x00,
        ]) + \
            self.part_num.to_bytes(2, byteorder='little') + \
            self.datalen.to_bytes(2, byteorder='little') + \
            self.data

    @property
    def is_start(self):
        return self.part_num == 0

    @property
    def header_size(self):
        return self.HEADER_SIZE

    @property
    def size(self):
        return self.header_size + self.datalen


class UpdateDeviceThread(QThread):
    captureSignal: pyqtSignal = pyqtSignal()

    def __init__(self, container, main_window):
        super().__init__()
        self.container = container
        self.main = main_window
        self.device = hid.device()
        try:
            self.device.open(VID, PID)
        except Exception as e:
            logging.error(f"Error opening device: {e}")
            raise
        QTimer.singleShot(0, self.start_timer)

    def start_timer(self):
        self.update_lcd_timer = QTimer()
        self.update_lcd_timer.timeout.connect(self.update_lcd)
        self.update_lcd_timer.start(int(1000 / 30))

    def run(self):
        self.exec()

    @pyqtSlot()
    def update_lcd(self):
        try:
            image = self.main.capture_container()

            width = image.width()
            height = image.height()
            ptr = image.bits()
            ptr.setsize(height * width * 4)
            arr = np.frombuffer(ptr, np.uint8).reshape((height, width, 4))

            arr = cv2.resize(arr, (480, 480))

            image_data = cv2.imencode('.jpg', arr)[1].tobytes()

            self.write_command(image_data)
        except Exception as e:
            logging.error(f"Error updating LCD: {e}")

    def make_commands(self, data, opcode=0x02, max_len=1024):
        real_max_len = max_len - CorsairCommand.HEADER_SIZE
        part_num = 0
        while data:
            if len(data) < real_max_len:
                padded_data = data + b'\x00' * (real_max_len - len(data))
            else:
                padded_data = data[:real_max_len]
            datalen = min(real_max_len, len(data))
            data = data[real_max_len:]
            try:
                yield CorsairCommand(
                    opcode=opcode,
                    unknown1=0x05,
                    unknown2=0x40,
                    is_end=not bool(data),
                    part_num=part_num,
                    datalen=datalen,
                    data=padded_data,
                )
            except Exception as e:
                logging.error(f"Error creating CorsairCommand: {e}")
                raise
            part_num += 1

    def write_command(self, data):
        try:
            commands = self.make_commands(data)
            for command in commands:
                self.device.write(command.to_bytes())
        except Exception as e:
            logging.error(f"Error writing command to device: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        logging.debug("Initializing MainWindow")
        self.pixmap_item = QGraphicsPixmapItem()
        self.pixmap_item.setPos(QPointF(0, 0))

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon('icon.ico'))

        self.window_state_handler = WindowStateHandler(self)
        if led_controller_enabled:
            self.led_controller = LEDController(self)
        else:
            logging.debug("LED controller is not enabled")

        self.current_image_path = None
        self.last_pixmap_pos = None
        self.last_transform = None
        self.last_slider_value = None
        self.last_scene_rect = None
        self.last_scrollbar_pos = None

        self.setWindowTitle('Corsair LCD Tool')
        self.setFixedSize(600, 650)

        self.container = QWidget(self)
        self.container.setGeometry(60, 20, 480, 480)
        self.container.setStyleSheet("background-color: #282c34; border: 0px")

        self.scene = QGraphicsScene(self.container)
        self.view = NoScrollGraphicsView(self.scene, self.container)
        self.view.setGeometry(0, 0, 480, 480)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        self.open_button = QPushButton("Open Image", self)
        self.open_button.setGeometry(60, 540, 120, 30)
        self.open_button.clicked.connect(self.open_image)

        self.tray_button = QPushButton("Minimize to Tray", self)
        self.tray_button.setGeometry(240, 540, 120, 30)
        self.tray_button.clicked.connect(self.window_state_handler.minimize_window)

        self.reset_button = QPushButton("Reset View", self)
        self.reset_button.setGeometry(420, 540, 120, 30)
        self.reset_button.clicked.connect(self.reset_image)

        self.script_path = os.path.join(os.getcwd(), 'corsair_lcd_tool.py')

        if platform.system() == "Windows":
            self.startup_folder = winshell.startup()

            self.shortcut_path = os.path.join(self.startup_folder, 'corsair_lcd_tool.lnk')
        else:
            pass

        self.python_path = sys.executable

        self.startup_checkbox = QCheckBox("Run at startup", self)
        self.startup_checkbox.setGeometry(60, 580, 120, 30)
        self.startup_checkbox.clicked.connect(self.update_startup)

        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setGeometry(60, 510, 480, 20)
        self.slider.setMinimum(20)
        self.slider.setMaximum(180)
        self.slider.setValue(100)
        self.slider.setDisabled(True)
        self.slider.valueChanged.connect(self.scale_image)

        restore_action = QAction("Restore", self)
        restore_action.triggered.connect(self.showNormal)

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.quit)

        self.tray_menu = QMenu()
        self.tray_menu.addAction(restore_action)
        self.tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(self.tray_menu)

        self.setWindowIcon(QIcon('icon.ico'))
        self.movie = None

        logging.debug("MainWindow initialized")

        try:
            self.updateDeviceThread = UpdateDeviceThread(self.container, self)
            self.updateDeviceThread.start()
            self.frames = []
        except Exception as e:
            logging.error(f"Error initializing MainWindow: {e}")
            raise

        QApplication.instance().setStyle(QStyleFactory.create('Fusion'))

        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor('#2c313a'))
        palette.setColor(QPalette.ColorRole.WindowText, QColor('white'))
        palette.setColor(QPalette.ColorRole.Base, QColor('#2c313a'))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor('gray'))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor('#2c313a'))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor('white'))
        palette.setColor(QPalette.ColorRole.Text, QColor('white'))
        palette.setColor(QPalette.ColorRole.Button, QColor('#2c313a'))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor('white'))
        palette.setColor(QPalette.ColorRole.BrightText, QColor('red'))
        palette.setColor(QPalette.ColorRole.Link, QColor('#0069c0'))
        palette.setColor(QPalette.ColorRole.Highlight, QColor('#0069c0'))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor('black'))
        QApplication.instance().setPalette(palette)

        self.save_state_handler = SaveStateHandler(self)
        self.save_state_handler.load_image_state()

        if not self.startup_checkbox.isChecked():
            self.show()
        else:
            QTimer.singleShot(5, self.window_state_handler.minimize_window)
        logging.debug("UI Initialized")

    def update_startup(self):
        state = self.startup_checkbox.isChecked()
        logging.debug(f"Checkbox state: {state}")
        logging.debug(f"Script path: {self.script_path}")
        self.save_state_handler.save_image_state()

        if platform.system() == "Windows":
            logging.debug(f"Shortcut path: {self.shortcut_path}")
            if state:
                with winshell.shortcut(self.shortcut_path) as shortcut:
                    shortcut.path = self.script_path
                    shortcut.working_directory = os.path.dirname(self.script_path)
                    shortcut.description = 'Corsair LCD Tool'
                logging.debug("Shortcut created.")
            else:
                if os.path.exists(self.shortcut_path):
                    os.remove(self.shortcut_path)
                    logging.debug("Shortcut removed.")
        elif platform.system() == "Linux":
            service_content = f"""[Unit]

    Description=Corsair LCD Tool

    [Service]
    ExecStart={self.python_path} {self.script_path}

    [Install]
    WantedBy=default.target
    """
            service_path = os.path.join(os.path.expanduser("~"), ".config/systemd/user", f"{os.path.basename(self.script_path)}.service")
            if state:
                with open(service_path, 'w') as f:
                    f.write(service_content)
                subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
                subprocess.run(["systemctl", "--user", "enable", os.path.basename(self.script_path)], check=True)
                logging.debug("Systemd user service created and enabled.")
            else:
                if os.path.exists(service_path):
                    os.remove(service_path)
                    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
                    logging.debug("Systemd user service removed.")

    def open_image(self):
        logging.debug("Entering open_image function")
        file_name, _ = QFileDialog.getOpenFileName(self, "Open Image", "",
                                                  "Images (*.png *.xpm *.jpg *.bmp *.gif)")
        if file_name:
            logging.debug(f"Opening image file: {file_name}")
            self.load_new_image(file_name)
            self.current_image_path = file_name
            self.save_state_handler.restart_save_image_state_timer()

    def load_new_image(self, file_name):
        logging.debug(f"Loading a new image: {file_name}")

        self.scene.clear()

        if file_name.lower().endswith('.gif'):
            self.load_new_gif(file_name)
        else:
            if self.movie is not None:
                self.movie.stop()
                self.movie.deleteLater()
                self.movie = None

            pixmap = QPixmap(file_name)

            self.pixmap_item = QGraphicsPixmapItem(pixmap)

            self.pixmap_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)

            self.scene.addItem(self.pixmap_item)

            self.slider.setEnabled(True)

    def load_new_gif(self, file_name):
        if self.movie is not None:
            self.movie.stop()
            self.movie.deleteLater()

        self.movie = QMovie(file_name)

        self.pixmap_item = QGraphicsPixmapItem()

        self.pixmap_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)

        self.scene.addItem(self.pixmap_item)

        self.movie.frameChanged.connect(lambda: self.pixmap_item.setPixmap(QPixmap.fromImage(self.movie.currentImage())))

        self.movie.start()

        self.slider.setEnabled(True)

    def reset_image(self):
        if self.current_image_path is not None:
            self.load_new_image(self.current_image_path)

    def scale_image(self, value):
        if self.pixmap_item is not None:
            self.pixmap_item.resetTransform()
            scale_factor = value / 100.0

            self.pixmap_item.setTransformOriginPoint(self.pixmap_item.boundingRect().center())

            self.pixmap_item.setScale(scale_factor)
            self.save_state_handler.restart_save_image_state_timer()

    def capture_container(self):
        try:
            pixmap = QPixmap(self.container.size())
            self.container.render(pixmap)
            return pixmap.toImage()
        except Exception as e:
            logging.error(f"Error in capture_container: {e}")
            return QImage()


class SaveStateHandler:
    def __init__(self, main_window):
        self.old_pos = None
        self.main = main_window
        self.is_first_load = True
        self.check_state_timer = QTimer()
        self.check_state_timer.setInterval(1000)
        self.check_state_timer.timeout.connect(self.check_state)
        self.save_image_state_timer = QTimer()
        self.save_image_state_timer.setInterval(2000)
        self.save_image_state_timer.timeout.connect(self.handle_save_image_state_timeout)
        self.save_image_state_flag = False
        self.old_transform = None

    def load_image_state(self):
        logging.debug("Entering load_image_state function")
        try:
            logging.debug(f"self.is_first_load value: {self.is_first_load}")
            if not self.is_first_load:
                logging.debug("State has already been loaded. Skipping load.")
                return

            state_file = 'state.yaml'
            if os.path.exists(state_file):
                try:
                    with open(state_file, 'r') as f:
                        state = yaml.safe_load(f)
                        logging.debug(f"Loaded state from file: {state}")

                    if state is not None:
                        image_path = state.get('lastImagePath', '')
                        if image_path and os.path.exists(image_path):
                            self.main.load_new_image(image_path)
                            self.main.current_image_path = image_path
                            self.main.view.resetTransform()
                            self.main.scene.setSceneRect(QRectF(*state.get('last_scene_rect')))
                            self.main.pixmap_item.setPos(QPointF(*state.get('last_pixmap_pos')))
                            self.main.view.setTransform(QTransform().scale(*state.get('last_transform')))
                            self.main.slider.setValue(state.get('last_slider_value'))
                            self.main.view.horizontalScrollBar().setValue(state.get('last_scrollbar_pos')[0])
                            self.main.view.verticalScrollBar().setValue(state.get('last_scrollbar_pos')[1])
                            self.main.startup_checkbox.setChecked(state.get('startup_checkbox_state', False))
                            logging.debug(f"Loaded state: {state}")
                        else:
                            logging.warning(
                                "State file is empty or image path does not exist. Loading with default settings.")
                    else:
                        logging.warning("No state file found. Loading with default settings.")
                except yaml.YAMLError as e:
                    logging.error(
                        f"Error loading state: Invalid YAML file. Loading with default settings. Error details: {e}")
        except Exception as e:
            logging.error(f"Error loading state: {e}")

        self.is_first_load = False
        self.check_state_timer.start()
        logging.debug('check_state_timer started')

    def save_image_state(self):
        try:
            if self.is_first_load:
                logging.debug("State loading is not complete. Skipping save_image_state.")
                return
            else:
                state = {
                    'lastImagePath': self.main.current_image_path,
                    'last_pixmap_pos': [self.main.pixmap_item.pos().x(), self.main.pixmap_item.pos().y()],
                    'last_transform': [self.main.view.transform().m11(), self.main.view.transform().m22()],
                    'last_slider_value': self.main.slider.value(),
                    'last_scene_rect': [self.main.scene.sceneRect().x(), self.main.scene.sceneRect().y(),
                                      self.main.scene.sceneRect().width(), self.main.scene.sceneRect().height()],
                    'last_scrollbar_pos': [self.main.view.horizontalScrollBar().value(),
                                         self.main.view.verticalScrollBar().value()],
                    'startup_checkbox_state': self.main.startup_checkbox.isChecked()
                }
                with open('state.yaml', 'w') as file:
                    yaml.dump(state, file)
                logging.debug(f"Saved state to disk: {state}")
        except Exception as e:
            logging.error(f"An error occurred while saving the image state: {e}")

    def check_state(self):
        try:
            newPos = self.main.pixmap_item.pos()
            newTransform = self.main.view.transform()
            if newPos is not None and newTransform is not None:
                if newPos != self.old_pos or newTransform != self.old_transform:
                    if not self.is_first_load:
                        self.restart_save_image_state_timer()
                    self.old_pos = newPos
                    self.old_transform = newTransform
            else:
                logging.warning("Either pixmap position or view transform is None.")
        except Exception as e:
            logging.error(f"Error in check_state: {e}")

    def handle_save_image_state_timeout(self):
        try:
            if self.save_image_state_flag:
                self.save_image_state()
                self.save_image_state_flag = False
        except Exception as e:
            logging.error(f"Error handling save state timeout: {e}")

    def restart_save_image_state_timer(self):
        try:
            if self.is_first_load:
                logging.debug("restart_save_image_state_timer skipped because is_first_load is True.")
                return
            if self.save_image_state_timer.isActive():
                self.save_image_state_timer.stop()
            self.save_image_state_timer.start()
            self.save_image_state_flag = True
            logging.debug(f"restart_save_image_state_timer called")
        except Exception as e:
            logging.error(f"Error in restart_save_image_state_timer: {e}")


class WindowStateHandler:
    def __init__(self, main_window):
        self.main = main_window
        self.main.tray_icon.activated.connect(self.handle_tray_activation)

    def handle_tray_activation(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            logging.debug("System tray icon activated. Restoring window.")
            self.restore_window()

    def restore_window(self):
        try:
            self.main.show()
        except Exception as e:
            logging.error(f"Error in restore_window: {e}")

    def minimize_window(self):
        try:
            logging.debug("Minimizing window to system tray.")
            self.main.hide()
            self.main.tray_icon.show()
        except Exception as e:
            logging.error(f"Error in minimize_window: {e}")


class NoScrollGraphicsView(QGraphicsView):
    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)

    def scroll_contents_by(self, dx, dy):
        pass

    def wheelEvent(self, event):
        pass


if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        logging.error(f"Error in main: {e}")

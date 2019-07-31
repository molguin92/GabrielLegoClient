#!/usr/bin/env python
from __future__ import absolute_import, division, print_function

import logging
import sys  # We need sys so that we can pass argv to QApplication
import threading
import time
from base64 import b64decode

import cv2
import fire
import logzero
import numpy as np
from logzero import logger
from PyQt4 import QtGui
from PyQt4.QtCore import QString, QThread, pyqtSignal, Qt
from PyQt4.QtGui import QImage, QPixmap

from client import Client
import design  # This file holds our MainWindow and all design related things


class UI(QtGui.QMainWindow, design.Ui_MainWindow):
    start_signal = pyqtSignal()

    def __init__(self):
        super(self.__class__, self).__init__()
        self.setupUi(self)  # This is defined in design.py file automatically
        self.started = False
        self.set_guidance(None, 'Press Enter to begin.')

    def keyPressEvent(self, event):
        super(self.__class__, self).keyPressEvent(event)
        if not self.started and (
                event.key() == Qt.Key_Enter or
                event.key() == Qt.Key_Return
        ):
            self.started = True
            self.start_signal.emit()

    @staticmethod
    def set_label_image(frame, label):
        img = QImage(
            frame, frame.shape[1], frame.shape[0], QtGui.QImage.Format_RGB888)
        pix = QPixmap.fromImage(img)
        label.setPixmap(pix)

    def update_video_feed(self, frame):
        UI.set_label_image(frame, self.feed_label)

    def set_guidance(self, image, instruction):
        if image is not None:
            UI.set_label_image(image, self.guidance_label)
        self.instruction_label.setText(QString(instruction))


class ClientThread(QThread, Client):
    sig_video_feed = pyqtSignal(object)
    sig_guidance_feed = pyqtSignal(object, str)

    def __init__(self, ip, ui, countdown_from=5):
        QThread.__init__(self)
        Client.__init__(self, ip=ip)

        # connect signals
        self.sig_video_feed.connect(ui.update_video_feed)
        self.sig_guidance_feed.connect(ui.set_guidance)
        ui.start_signal.connect(self.start)
        self.countdown_from = countdown_from

    def video_frame_callback(self, frame):
        self.sig_video_feed.emit(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

    def response_callback(self, resp_dict):
        instruction = resp_dict.get('speech', '')
        guidance = resp_dict.get('animation', [])

        if len(instruction) > 0 and len(guidance) > 0:
            logger.info('instruction: {}'.format(instruction))

            if len(guidance[-1]) > 0:
                guidance = b64decode(guidance[-1][0])
                np_data = np.fromstring(guidance, dtype=np.uint8)
                frame = cv2.imdecode(np_data, cv2.CV_LOAD_IMAGE_COLOR)
                guidance = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                self.sig_guidance_feed.emit(guidance, instruction)

    def run(self):
        # countdown before starting the experiment
        for i in range(self.countdown_from, 0, -1):
            ti = time.time()
            self.sig_guidance_feed.emit(None, '{}'.format(i))
            time.sleep(max(1.0 - (time.time() - ti), 0))

        self.sig_guidance_feed.emit(None, '')
        super(ClientThread, self).connect_and_run()

    def stop(self):
        self.shutdown_event.set()


def main(ip, *args, **kwargs):
    app = QtGui.QApplication(sys.argv)
    ui = UI()
    ui.show()
    clientThread = ClientThread(ip, ui)

    app.exec_()

    clientThread.stop()
    clientThread.wait()


if __name__ == '__main__':  # if we're running file directly and not
    # importing it
    logzero.loglevel(logging.INFO)
    fire.Fire(main)

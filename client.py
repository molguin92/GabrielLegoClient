#! /usr/bin/env python

from __future__ import absolute_import, division, print_function

import Queue
import cv2
import json
import logging
import select
import struct
import threading
import time
from time import sleep

import fire
import logzero
from logzero import logger

import protocol
from config import Config
from performance import TaskMonitor
from socketLib import ClientCommand, ClientReply, SocketClientThread


class GabrielSocketCommand(ClientCommand):
    STREAM = len(ClientCommand.ACTIONS)
    ACTIONS = ClientCommand.ACTIONS + [STREAM]
    LISTEN = len(ACTIONS)
    ACTIONS.append(LISTEN)

    def __init__(self, type, data=None):
        super(GabrielSocketCommand, self).__init__(type, data=data)


class VideoCaptureThread(threading.Thread):
    """
    Thread tasked with capturing video from the camera at a fixed rate.
    """

    def __init__(self,
                 input_source,
                 fps=24,
                 video_frame_callback=None):
        super(VideoCaptureThread, self).__init__()
        self.input_source = input_source
        self.video_frame_callback = video_frame_callback
        self.alive = threading.Event()
        self.alive.set()
        self.frame_buf = Queue.Queue(maxsize=1)  # holds latest frame
        self.interval = 1.0 / float(fps)
        self.daemon = True

    def run(self):
        video_capture = cv2.VideoCapture(self.input_source)
        while self.alive.isSet():
            ti = time.time()
            ret, frame = video_capture.read()

            if ret:
                if self.video_frame_callback:
                    self.video_frame_callback(frame)
                    # self.sig_feed.emit(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

                self._put_frame((ret, frame))
                time.sleep(max(self.interval - (time.time() - ti), 0))
            else:
                logger.debug(
                    'No more video frames from {}'.format(self.input_source))

                self._put_frame((ret, None))
                break

        video_capture.release()

    def _put_frame(self, frame):
        while True:
            try:
                self.frame_buf.put(frame, block=False)
                return
            except Queue.Full:
                self.frame_buf.get()

    def get_frame(self):
        return self.frame_buf.get(block=True)

    def join(self, timeout=None):
        self.alive.clear()
        threading.Thread.join(self, timeout)


class VideoStreamingThread(SocketClientThread):
    def __init__(self, video_capture, task_monitor,
                 cmd_q=None, reply_q=None):
        super(VideoStreamingThread, self).__init__(cmd_q, reply_q)
        self.handlers[GabrielSocketCommand.STREAM] = self._handle_STREAM
        self.is_streaming = False
        self.video_capture = video_capture
        self.task_monitor = task_monitor

    def run(self):
        while self.alive.isSet():
            try:
                cmd = self.cmd_q.get(True, 0.1)
                self.handlers[cmd.type](cmd)
            except Queue.Empty as e:
                continue

    # tokenm: token manager
    def _handle_STREAM(self, cmd):
        tokenm = cmd.data
        self.is_streaming = True
        id = 0
        while self.alive.isSet() and self.is_streaming:
            # will be put into sleep if token is not available
            tokenm.getToken()
            ret, frame = self.video_capture.get_frame()
            if not ret:
                break
            ret, jpeg_frame = cv2.imencode('.jpg', frame)
            header = {protocol.Protocol_client.JSON_KEY_FRAME_ID: str(id)}
            header_json = json.dumps(header)
            self.task_monitor.register_sent_frame(id)
            self._handle_SEND(ClientCommand(ClientCommand.SEND, header_json))
            self._handle_SEND(ClientCommand(
                ClientCommand.SEND, jpeg_frame.tostring()))
            logger.debug('Send Frame {}'.format(id))
            id += 1


class ResultReceivingThread(SocketClientThread):
    def __init__(self, cmd_q=None, reply_q=None, legacy=Config.LEGACY):
        super(ResultReceivingThread, self).__init__(cmd_q, reply_q)
        self.handlers[GabrielSocketCommand.LISTEN] = self._handle_LISTEN
        self.is_listening = False
        self.legacy = legacy

    def run(self):
        while self.alive.isSet():
            try:
                cmd = self.cmd_q.get(True, 0.1)
                self.handlers[cmd.type](cmd)
            except Queue.Empty as e:
                continue

    def _handle_LISTEN(self, cmd):
        tokenm = cmd.data
        self.is_listening = True
        while self.alive.isSet() and self.is_listening:
            if self.socket:
                input = [self.socket]
                inputready, outputready, exceptready = select.select(
                    input, [], [])
                for s in inputready:
                    if s == self.socket:
                        # handle the server socket
                        header, data = self._recv_gabriel_data()
                        self.reply_q.put(self._success_reply((header, data)))
                        tokenm.putToken()

    def _recv_gabriel_data(self):
        header_size = struct.unpack("!I", self._recv_n_bytes(4))[0]
        header = self._recv_n_bytes(header_size)
        header_json = json.loads(header)
        if self.legacy:
            data = header_json.pop('result')
        else:
            data_size = header_json['data_size']
            data = self._recv_n_bytes(data_size)

        return header_json, data


class TokenManager(object):
    """Implements Gabriel's token mechanism."""

    def __init__(self, token_num):
        super(TokenManager, self).__init__()
        self.token_num = token_num
        # token val is [0..token_num)
        self.token_val = token_num - 1
        self.lock = threading.Lock()
        self.has_token_cv = threading.Condition(self.lock)

    def _inc(self):
        self.token_val = (self.token_val + 1) if (self.token_val <
                                                  self.token_num) else (
            self.token_val)

    def _dec(self):
        self.token_val = (
                self.token_val - 1) if (self.token_val >= 0) else (
            self.token_val)

    def empty(self):
        return (self.token_val < 0)

    def getToken(self):
        with self.has_token_cv:
            while self.token_val < 0:
                self.has_token_cv.wait()
            self._dec()

    def putToken(self):
        with self.has_token_cv:
            self._inc()
            if self.token_val >= 0:
                self.has_token_cv.notifyAll()


class Client(object):
    def __init__(self,
                 ip=Config.GABRIEL_IP,
                 video_input=0,
                 legacy=Config.LEGACY,
                 video_port=Config.VIDEO_STREAM_PORT,
                 result_port=Config.RESULT_RECEIVING_PORT,
                 num_tokens=Config.TOKEN
                 ):
        super(self.__class__, self).__init__()
        self.ip = ip
        self.video_input = video_input
        self.legacy = legacy
        self.video_port = video_port
        self.result_port = result_port
        self.token_mgr = TokenManager(num_tokens)

        self.shutdown_event = threading.Event()
        self.shutdown_event.clear()

        self.current_step = -1

    def video_frame_callback(self, frame):
        # no-op by default
        logger.info('Superclass...')
        pass

    def response_callback(self, resp_dict):
        instruction = resp_dict.get('speech', False)
        if instruction and len(instruction > 0):
            logger.info('instruction: {}'.format(instruction))

    @staticmethod
    def parse(data):
        if Config.LEGACY:
            return json.loads(data)
        else:
            return data

    def connect_and_run(self):
        # todo: parameterize location
        with TaskMonitor('testing', working_dir='/opt/mnt/') as task_monitor:
            logger.debug(
                "Connecting to Server ({}) Port ({}, {})".format(self.ip,
                                                                 self.video_port,
                                                                 self.result_port))

            # create the video threads
            stream_cmd_q = Queue.Queue()
            video_capture_thread = VideoCaptureThread(
                self.video_input,
                video_frame_callback=self.video_frame_callback
            )
            video_streaming_thread = VideoStreamingThread(video_capture_thread,
                                                          task_monitor,
                                                          cmd_q=stream_cmd_q)
            video_streaming_thread.daemon = True

            # connect and stream to server
            stream_cmd_q.put(ClientCommand(ClientCommand.CONNECT,
                                           (self.ip, self.video_port)))
            stream_cmd_q.put(ClientCommand(GabrielSocketCommand.STREAM,
                                           self.token_mgr))

            # create listening threads
            result_cmd_q = Queue.Queue()
            result_reply_q = Queue.Queue()
            result_receiving_thread = ResultReceivingThread(
                cmd_q=result_cmd_q, reply_q=result_reply_q, legacy=self.legacy)
            result_receiving_thread.daemon = True

            result_cmd_q.put(ClientCommand(ClientCommand.CONNECT,
                                           (self.ip, self.result_port)))
            result_cmd_q.put(ClientCommand(GabrielSocketCommand.LISTEN,
                                           self.token_mgr))

            video_capture_thread.start()
            result_receiving_thread.start()
            sleep(0.1)
            video_streaming_thread.start()

            def join_threads():
                video_streaming_thread.join()
                result_receiving_thread.join()
                video_capture_thread.join()
                with self.token_mgr.has_token_cv:
                    self.token_mgr.has_token_cv.notifyAll()

            self.current_step = 0

            while not self.shutdown_event.is_set():
                try:
                    resp = result_reply_q.get(timeout=0.01)

                    # connect and send also send reply to reply queue without
                    # any data attached
                    if resp.type == ClientReply.SUCCESS \
                            and resp.data is not None:
                        (resp_header, resp_data) = resp.data
                        logger.debug('header: {}'.format(resp_header))

                        parsed_data = Client.parse(resp_data)
                        step = parsed_data.get('state_index', -1)
                        task_monitor.register_reply_recv(
                            resp_header['frame_id'], step
                        )

                        self.response_callback(parsed_data)

                    elif resp.type == ClientReply.ERROR:
                        logger.error("Error: {}".format(resp.data))
                        join_threads()
                        break
                except Queue.Empty:
                    continue
                except KeyboardInterrupt:
                    break

        join_threads()


if __name__ == '__main__':
    logzero.loglevel(logging.INFO)
    fire.Fire(Client, 'connect_and_run')

import json
import time
from collections import namedtuple
from threading import RLock

from pathlib2 import Path

FrameRecord = namedtuple('FrameRecord', ['index', 'send', 'recv'])


class TaskStepRecord(object):
    def __init__(self, step_index):
        super(TaskStepRecord, self).__init__()
        self.index = step_index
        self.frames = []
        self.start = -1
        self.end = -1
        self.duration = 0

    def add_frame(self, frame):
        self.frames.append(frame)
        if self.start < 0:
            self.start = frame.send

        self.end = frame.recv
        self.duration = self.end - self.start

    def to_dict(self):
        d = self.__dict__
        frames = [f.__dict__ for f in d.get('frames', [])]
        d['frames'] = frames
        return d


class ErrorRecord(TaskStepRecord):
    def __init__(self, step_index):
        super(ErrorRecord, self).__init__(step_index=-1)
        self.previous_step_index = step_index


class TaskMonitor(object):
    def __init__(self, run_id, working_dir=Path.cwd()):
        super(TaskMonitor, self).__init__()
        self.current_step = TaskStepRecord(step_index=0)
        self.previous_steps = []
        self.run_id = run_id
        self.working_dir = working_dir
        self.frames_in_flight = {}
        self.lock = RLock()
        self.init = -1

    def register_sent_frame(self, frame_id):
        with self.lock:
            self.frames_in_flight[frame_id] = time.time()

    def register_reply_recv(self, recv_id, step_index):
        recv_time = time.time()
        with self.lock:
            sent_time = self.frames_in_flight[recv_id]
            self.frames_in_flight.pop(recv_id)

            self.current_step.add_frame(
                FrameRecord(recv_id, sent_time, recv_time))

            if self.current_step.index != step_index:
                self.previous_steps.append(self.current_step)
                self.current_step = TaskStepRecord(step_index) \
                    if step_index != -1 else ErrorRecord(step_index)

    def __enter__(self):
        self.init = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        with self.lock:
            self.previous_steps.append(self.current_step)
            self.current_step = None

            file_path = str(
                Path(self.working_dir, '{}.json'.format(self.run_id)))
            with open(file_path, 'w') as f:
                json.dump(
                    obj={
                        'name'  : self.run_id,
                        'init'  : self.init,
                        'finish': time.time(),
                        'steps' : [step.to_dict() for step in
                                   self.previous_steps]
                    },
                    fp=f)

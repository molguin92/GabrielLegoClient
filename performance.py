import json
import time
from collections import namedtuple
from csv import DictWriter
from threading import Thread, Event
import Queue
from pathlib2 import Path
from enum import Enum, unique


# LOG format:
# CSV
# Columns: index, timestamp, type, extra_info

class StrEnum(str, Enum):
    pass


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
        self.frame_in_flight = None

        self.init = -1

    def register_sent_frame(self, frame_id):
        self.frame_in_flight = (frame_id, time.time())

    def register_reply_recv(self, recv_id, step_index):
        recv_time = time.time()
        sent_id, sent_time = self.frame_in_flight

        assert sent_id == recv_id
        self.frame_in_flight = None

        self.current_step.add_frame(FrameRecord(recv_id, sent_time, recv_time))

        if self.current_step.index != step_index:
            self.previous_steps.append(self.current_step)
            self.current_step = TaskStepRecord(step_index) \
                if step_index != -1 else ErrorRecord(step_index)

    def __enter__(self):
        self.init = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.previous_steps.append(self.current_step)
        self.current_step = None

        file_path = str(Path(self.working_dir, '{}.json'.format(self.run_id)))
        with open(file_path, 'w') as f:
            json.dump(
                obj={
                    'name'  : self.run_id,
                    'init'  : self.init,
                    'finish': time.time(),
                    'steps' : [step.to_dict() for step in self.previous_steps]
                },
                fp=f)


class PerformanceLogger(object):
    PATH_FMT = '{}.log.csv'
    QUEUE_TIMEOUT = 0.1
    CSV_COLUMNS = [
        'timestamp',
        'type',
        'extra_info'
    ]

    @unique
    class LogType(StrEnum):
        start = 'START'
        error = 'ERROR'
        step_chg = 'STEP_CHANGE'
        abort = 'ABORT'
        end = 'END'

    Record = namedtuple('Record', CSV_COLUMNS)

    def __init__(self, run_name, file_dir=Path.cwd()):
        super(PerformanceLogger, self).__init__()

        self.start_time = time.time()
        self.shutdown_signal = Event()
        self.queue = Queue.Queue()

        # put start record in log
        self.queue.put(PerformanceLogger.Record(
            timestamp=0.0,
            type=PerformanceLogger.LogType.start.value(),
            extra_info=None
        ))

        self.worker = Thread(target=PerformanceLogger.__worker_loop,
                             args=(self,))
        self.log_file_path = Path(file_dir,
                                  PerformanceLogger.PATH_FMT.format(run_name))

    def log(self, record_type, extra_info=None):
        timestamp = time.time() - self.start_time
        assert isinstance(record_type, PerformanceLogger.LogType)

        self.queue.put(PerformanceLogger.Record(
            timestamp=timestamp,
            type=record_type.value,
            extra_info=extra_info
        ))

    def __worker_loop(self):
        with open(str(self.log_file_path), 'w') as f:
            writer = DictWriter(f, fieldnames=PerformanceLogger.CSV_COLUMNS)
            writer.writeheader()

            while not self.shutdown_signal.is_set():
                try:
                    record = self.queue.get(
                        timeout=PerformanceLogger.QUEUE_TIMEOUT)
                    writer.writerow(record._asdict())
                except Queue.Empty:
                    continue

            while not self.queue.empty():
                record = self.queue.get()
                writer.writerow(record._asdict())

    def __enter__(self):
        self.worker.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):

        # put end record in log
        self.queue.put(PerformanceLogger.Record(
            timestamp=time.time() - self.start_time,
            type=PerformanceLogger.LogType.end.value(),
            extra_info=None
        ))

        self.shutdown_signal.set()
        self.worker.join()


if __name__ == '__main__':
    with PerformanceLogger('test') as logger:
        logger.log(PerformanceLogger.LogType.error)
        logger.log(PerformanceLogger.LogType.step_chg)
        logger.log(PerformanceLogger.LogType.abort)

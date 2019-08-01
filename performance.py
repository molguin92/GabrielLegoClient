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


FrameRecord = namedtuple('FrameRecord',
                         [])


class TaskMonitor(object):
    def __init__(self):
        super(TaskMonitor, self).__init__()
        self.latest_frame = None

    def register_sent_frame(self, frame_id):
        self.latest_frame = (frame_id, time.time())

    def register_reply_recv(self, frame_id, data):
        recv_time = time.time()
        sent_id, sent_time = self.latest_frame
        assert frame_id == sent_id

        # todo analyze reply data


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

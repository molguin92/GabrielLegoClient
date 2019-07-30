import time
from csv import DictWriter
from threading import Thread, Event
import Queue
from pathlib import Path
from enum import Enum, unique


# LOG format:
# CSV
# Columns: index, timestamp, type, extra_info

class StrEnum(str, Enum):
    pass


class PerformanceLogger(object):
    PATH_FMT = '{}.log.csv'
    QUEUE_TIMEOUT = 0.1
    CSV_COLUMNS = [
        'index',
        'timestamp',
        'type',
        'extra_info'
    ]

    @unique
    class LogType(StrEnum):
        error = 'ERROR'
        step_chg = 'STEP_CHANGE'
        abort = 'ABORT'

    def __init__(self, run_name, file_dir=Path.cwd()):
        super(PerformanceLogger, self).__init__()

        self.start_time = time.time()
        self.shutdown_signal = Event()
        self.queue = Queue.Queue()

        # put start record in log
        self.queue.put({
            'timestamp' : self.start_time,
            'type'      : 'START',
            'extra_info': None
        })

        self.worker = Thread(target=PerformanceLogger.__worker_loop,
                             args=(self,))
        self.log_file_path = Path(file_dir,
                                  PerformanceLogger.PATH_FMT.format(run_name))

    def log(self, record_type, extra_info=None):
        timestamp = time.time() - self.start_time
        if not isinstance(record_type, PerformanceLogger.LogType):
            # todo throw a cute error?
            return

        self.queue.put({
            'timestamp' : timestamp,
            'type'      : record_type.value,
            'extra_info': extra_info
        })

    def __worker_loop(self):
        with open(self.log_file_path, 'w') as f:
            writer = DictWriter(f, fieldnames=PerformanceLogger.CSV_COLUMNS)
            writer.writeheader()

            idx = 0

            while not self.shutdown_signal.is_set():
                try:
                    record = self.queue.get(
                        timeout=PerformanceLogger.QUEUE_TIMEOUT)
                    record['index'] = idx
                    idx += 1
                    writer.writerow(record)
                except Queue.Empty:
                    continue

            while not self.queue.empty():
                record = self.queue.get()
                writer.writerow(record)

    def __enter__(self):
        self.worker.start()

    def __exit__(self, exc_type, exc_val, exc_tb):

        # put end record in log
        self.queue.put({
            'timestamp' : self.start_time,
            'type'      : 'END',
            'extra_info': None
        })

        self.shutdown_signal.set()
        self.worker.join()


if __name__ == '__main__':
    with PerformanceLogger('test') as logger:
        logger.log(PerformanceLogger.LogType.error)
        logger.log(PerformanceLogger.LogType.step_chg)
        logger.log(PerformanceLogger.LogType.abort)

import time
from threading import Thread, Event
import Queue
from pathlib import Path


class PerformanceLogger(object):
    PATH_FMT = '{}.log'
    QUEUE_TIMEOUT = 0.1

    def __init__(self, run_name, file_dir=Path.cwd()):
        super(PerformanceLogger, self).__init__()
        self.shutdown_signal = Event()
        self.queue = Queue.Queue()
        self.worker = Thread(target=PerformanceLogger.__worker_loop,
                             args=(self,))
        self.log_file_path = Path(file_dir,
                                  PerformanceLogger.PATH_FMT.format(run_name))

    def log(self, record_type):
        # todo impl correctly
        self.queue.put(LogRecord('test'))

    def __worker_loop(self):
        with open(self.log_file_path, 'w') as f:
            while not self.shutdown_signal.is_set():
                try:
                    record = self.queue.get(
                        timeout=PerformanceLogger.QUEUE_TIMEOUT)
                    f.write(str(record))
                    f.write('\n')
                except Queue.Empty:
                    continue

            while not self.queue.empty():
                record = self.queue.get()
                f.write(str(record))
                f.write('\n')

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.shutdown_signal.set()
        self.worker.join()


class LogRecord(object):
    def __init__(self, msg, t_0=0):
        super(LogRecord, self).__init__()
        self.timestamp = time.time() - t_0
        self.msg = msg

    def __str__(self):
        return '{}: {}'.format(self.timestamp, self.msg)

from Queue import Queue
from datetime import datetime
import functools
import inspect
import socket
import threading

_IP = "127.0.0.1"

_SOCKET_BUFFER_SIZE = 100

# -1 == no size restriction on the queue
_QUEUE_MAX_SIZE = -1


class Event:
    def __init__(self, timestamp, data):
        self.original_data = data
        self.timestamp = timestamp
        data = self.original_data.split(";")[0]
        self.data = data.split()
        self.channel = self.data[0]
        self.args = [self._coerce(arg) for arg in self.data[1:]]

    @property
    def first_arg(self):
        return self.args[0]

    def __repr__(self):
        return "<timestamp %d, data=%s>" % (self.timestamp, self.data)

    def save(self, fp):
        single_line = self.original_data.replace("\n", "")
        fp.write("%d %s\n" % (self.timestamp, single_line))

    def _coerce(self, param):
        if '"' in param:
            return param.replace('"', '')
        if '.' in param:
            return float(param)
        else:
            return int(param)


class DefaultTimer:
    def __init__(self):
        self.start_time = None
        self.dt = 0
        self.frame_index = 0

    def start(self):
        self.start_time = datetime.now()

    def update(self, dt):
        self.dt = dt * 1000
        self.frame_index += 1

    def get_current_time(self):
        if self.start_time == None: raise ValueError, "Timer hasn't been started! call start before using it."
        delta = datetime.now() - self.start_time
        # TODO: BUGGY this one will wrap around after 24 hours!
        return delta.seconds * 1000 + (delta.microseconds / 1000)

    def is_started(self):
        return self.start_time is not None


class TcpServer:
    def __init__(self, port):
        self._port = port
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def connect(self):
        self._socket.bind((_IP, self._port))
        self._socket.listen(1)
        print("Waiting for TCP connection...")
        self._socket, addr = self._socket.accept()
        print('Connection address:', addr)

    def receive(self):
        return self._socket.recv(_SOCKET_BUFFER_SIZE)

    def disconnect(self):
        self._socket.close()


class UdpServer:
    def __init__(self, port):
        self._port = port
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def connect(self):
        self._socket.bind((_IP, self._port))
        print("Waiting for UDP connection...")

    def receive(self):
        data, conn = self._socket.recvfrom(_SOCKET_BUFFER_SIZE)
        return data

    def disconnect(self):
        self._socket.close()


def receiver_loop(server, producer_queue, timestamp_func, verbose):
    server.connect()

    try:
        while 1:
            data = server.receive()
            if not data: break
            if verbose: print("received data:", data)
            if data[-1] == '\n':
                event = Event(timestamp_func(), data)
                producer_queue.put(event)
            else:
                raise Exception("Too large data object received. Max size: %d." % _SOCKET_BUFFER_SIZE)
    finally:
        server.disconnect()


def patch_replayer(fp, producer_queue, timestamp_func):
    for line in fp.readlines():
        parts = line.split()
        timestamp = int(parts[0])
        payload = parts[1:]

        while timestamp_func() < timestamp:
            pass

        event = Event(timestamp_func(), ' '.join(payload))
        producer_queue.put(event)


class DefaultEventHandler:
    def __init__(self, verbose):
        self.verbose = verbose

    def handle_event(self, event):
        if self.verbose: print("Unhandled event: %s" % event)


class TriggerFuncWrapper:
    def __init__(self, func):
        self.func = func

        args, varargs, varkw, defaults = inspect.getargspec(self.func)

        self.trigger_args = args

        if varargs:
            raise Exception("Trigger currently doesn't support *args")
        if varkw:
            raise Exception("Trigger currently doesn't support **args")
        if defaults:
            raise Exception("Trigger currently doesn't support default arguments")

    def trigger(self, event):
        event_args = event.args
        args = event_args[:len(self.trigger_args)]
        self.func(*args)


class Trigger:
    def __init__(self, *funcs):
        self.trigger_wrappers = [TriggerFuncWrapper(func) for func in funcs]

    def handle_event(self, event):
        for t in self.trigger_wrappers:
            t.trigger(event)


class Slider:
    def __init__(self, min_value=0.0, max_value=1.0):
        self.min_value = min_value
        self.max_value = max_value
        self.value = 0
        self._changed = True

    def handle_event(self, event):
        self._changed = True
        self.value = event.first_arg

    @property
    def changed(self):
        changed = self._changed
        self._changed = False
        return changed


class Patch:
    def __init__(self, consumer_queue, verbose=False):
        self._queue = consumer_queue
        self._recorded_events = []
        self.event_handlers = {}
        self.default_handler = DefaultEventHandler(verbose)

    def bind(self, channel, event_handler):
        self.event_handlers[channel] = event_handler
        return event_handler

    def route_events(self):
        while self.contains_events:
            event = self.pop_event()
            handler = self.event_handlers.get(int(event.channel), self.default_handler)
            handler.handle_event(event)

    def save_events(self, fp):
        for event in self._recorded_events:
            event.save(fp)

    @property
    def contains_events(self):
        return self._queue.qsize() > 0

    def pop_event(self):
        event = self._queue.get(block=False)
        self._recorded_events.append(event)
        return event


def create_remote_patch(port=13000, verbose_listener=False, verbose_patch=False, use_udp=False):
    queue = Queue(_QUEUE_MAX_SIZE)
    timer = DefaultTimer()
    patch = Patch(queue, verbose_patch)

    if use_udp:
        server = UdpServer(port=port)
    else:
        server = TcpServer(port=port)

    server_thread = functools.partial(receiver_loop,
                                      server,
                                      producer_queue=queue,
                                      timestamp_func=timer.get_current_time,
                                      verbose=verbose_listener)

    t = threading.Thread(target=server_thread)
    t.daemon = True
    timer.start()
    t.start()
    return patch


def replay_patch(event_filename, verbose_patch=False):
    queue = Queue(_QUEUE_MAX_SIZE)
    timer = DefaultTimer()
    patch = Patch(queue, verbose_patch)

    fp = open(event_filename, "r")

    replayer_thread = functools.partial(patch_replayer,
                                        fp, queue,
                                        timestamp_func=timer.get_current_time)

    t = threading.Thread(target=replayer_thread)
    t.daemon = True
    timer.start()
    t.start()
    return patch

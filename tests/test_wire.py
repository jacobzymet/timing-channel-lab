import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPResponse

import pytest

from timingchannel.resource import ChannelHandler, ChannelServer
from timingchannel.wire import ping_once, post_with_hold, wait_for_resource


@pytest.fixture
def resource():
    with ChannelServer(("127.0.0.1", 0), 64, timeout_s=0.25) as server:
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        try:
            yield server
        finally:
            server.shutdown()
            worker.join(timeout=2)
            assert not worker.is_alive()


def test_shared_worker_exposes_body_hold_as_ping_latency(resource, monkeypatch):
    entered = threading.Event()
    original = ChannelHandler.do_POST

    def mark_work(handler):
        entered.set()
        original(handler)

    monkeypatch.setattr(ChannelHandler, "do_POST", mark_work)
    host, port = resource.server_address
    with ThreadPoolExecutor(max_workers=1) as executor:
        sending = executor.submit(post_with_hold, host, port, b"x" * 64, 150, 2)
        assert entered.wait(2)
        latency, failed = ping_once(host, port, 2)
        assert not failed
        assert latency >= 70
        sending.result(timeout=2)


def test_rejected_work_is_not_counted_as_success(resource):
    # The peer may close before sendall(body), producing a connection reset
    # rather than a parsed HTTP error. Both must remain failed work.
    with pytest.raises(OSError):
        post_with_hold(*resource.server_address, b"wrong size", 0, 2)
    with socket.create_connection(resource.server_address, timeout=2) as client:
        client.sendall(b"POST /work HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1\r\n\r\n")
        with HTTPResponse(client) as response:
            response.begin()
            assert response.status == 400


def test_http_failure_does_not_signal_readiness(resource, monkeypatch):
    monkeypatch.setattr(ChannelHandler, "do_GET", lambda handler: handler._empty_response(503))
    assert ping_once(*resource.server_address, 1)[1]
    with pytest.raises(TimeoutError):
        wait_for_resource(*resource.server_address, timeout_s=0.05)


def test_abandoned_body_cannot_stall_worker_forever(resource):
    host, port = resource.server_address
    with socket.create_connection((host, port), timeout=2) as stalled:
        stalled.sendall(b"POST /work HTTP/1.1\r\nHost: localhost\r\nContent-Length: 64\r\n\r\n")
        assert not ping_once(host, port, 2)[1]
        assert b"408" in stalled.recv(4096)

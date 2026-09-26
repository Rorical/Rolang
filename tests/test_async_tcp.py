"""TCP operations exercise the real scheduler and loopback sockets."""
import pytest
from rolang.driver import OptLevel
from test_async_tasks import build_run


@pytest.mark.parametrize('level', list(OptLevel))
@pytest.mark.parametrize('address', ['127.0.0.1', '::1'])
def test_tcp_roundtrip_and_cancel_accept(tmp_path, level, address):
    run = build_run(tmp_path, '''
import std.async_io
import std.task
import std.result
import std.io
def client(port: i32) async -> i32 {
    switch await AsyncStream.connect("ADDRESS", port) {
        case .err(let e): return e;
        case .ok(let stream):
            switch await stream.write("hello") {
                case .err(let e): return e;
                case .ok(let n): if n != 5 { return 1; }
            }
            stream.shutdown_write();
            switch await stream.read(10) {
                case .err(let e): return e;
                case .ok(let text): println(text);
            }
    }
    return 0;
}
def main() async -> i32 {
    switch AsyncListener.bind("ADDRESS", 0, 16) {
        case .err(let e): return e;
        case .ok(let listener):
            if listener.port() <= 0 { return 2; }
            let pending = spawn listener.accept();
            await sleep(2);
            if pending.done() { return 3; }
            pending.cancel();
            if await pending.wait() { return 4; }
            let worker = spawn client(listener.port());
            switch await listener.accept() {
                case .err(let e): return e;
                case .ok(let stream):
                    var text = "";
                    while true {
                        switch await stream.read(2) {
                            case .err(let e): return e;
                            case .ok(let chunk):
                                if chunk.len() == 0 { break; }
                                text = text + chunk;
                        }
                    }
                    println(text);
                    switch await stream.write("ok") {
                        case .err(let e): return e;
                        case .ok(let n): if n != 2 { return 5; }
                    }
            }
            return await worker;
    }
}
'''.replace('ADDRESS', address), level)
    assert (run.returncode, run.stdout, run.stderr) == (0, 'hello\nok\n', '')


@pytest.mark.parametrize('level', list(OptLevel))
def test_invalid_addresses_ports_and_refused_connection(tmp_path, level):
    run = build_run(tmp_path, '''
import std.async_io
import std.task
import std.result
def closed_port() -> i32 {
    switch AsyncListener.bind("127.0.0.1", 0, 8) {
        case .ok(let listener): return listener.port();
        case .err(let e): return -e;
    }
}
def main() async -> i32 {
    switch await AsyncStream.connect("localhost", 80) {
        case .ok(let s): return 1;
        case .err(let e): if e <= 0 { return 2; }
    }
    switch await AsyncStream.connect("127.0.0.1", 65536) {
        case .ok(let s): return 3;
        case .err(let e): if e <= 0 { return 4; }
    }
    switch AsyncListener.bind("bad", 0, 8) {
        case .ok(let s): return 5;
        case .err(let e): if e <= 0 { return 6; }
    }
    switch AsyncListener.bind("127.0.0.1", 0, 0) {
        case .ok(let s): return 7;
        case .err(let e): if e <= 0 { return 8; }
    }
    let port = closed_port();
    if port <= 0 { return 9; }
    switch await AsyncStream.connect("127.0.0.1", port) {
        case .ok(let s): return 10;
        case .err(let e): if e <= 0 { return 11; }
    }
    return 0;
}
''', level)
    assert (run.returncode, run.stderr) == (0, '')


def test_native_tcp_descriptor_ownership(tmp_path):
    """Check actual descriptor closure, including unclaimed native results."""
    import subprocess
    from pathlib import Path
    import rolang

    runtime = Path(rolang.__file__).parent / 'runtime' / 'rolang_rt.c'
    source = tmp_path / 'ownership.c'
    source.write_text('#include "' + str(runtime) + '"\n' + r'''
#include <assert.h>
static void closed_fd(int fd) {
    errno = 0;
    assert(fcntl(fd, F_GETFD) == -1 && errno == EBADF);
}
int32_t __rolang_user_main(void) {
    struct { ObjHeader header; StringPayload value; } address = {0};
    address.value.data = "127.0.0.1"; address.value.len = 9;
    for (int i = 0; i < 100; i++) {
        void* listener = NULL;
        assert(rt_async_listener_bind(&address, 0, 8, &listener) == 0);
        int port = rt_async_listener_port(listener);
        int listener_fd = ((AsyncStream*)listener)->fd;
        TaskHandle* accept_task = rt_async_accept_start(listener);
        TaskHandle* connect_task = rt_async_connect_start(&address, port);
        assert(*(int32_t*)rt_task_join(connect_task) == 0);
        assert(*(int32_t*)rt_task_join(accept_task) == 0);
        int accepted_fd = accept_task->result_stream->fd;
        int connected_fd = connect_task->result_stream->fd;
        /* A successful but unclaimed accept must close its socket. */
        rt_task_destroy(accept_task);
        closed_fd(accepted_fd);
        AsyncStream* connected = rt_async_take_stream(connect_task);
        rt_task_destroy(connect_task);
        assert(fcntl(connected_fd, F_GETFD) >= 0);
        rt_async_stream_close(connected);
        closed_fd(connected_fd);
        /* Cancelling before observing connect completion closes its socket. */
        connect_task = rt_async_connect_start(&address, port);
        connected_fd = connect_task->stream->fd;
        rt_task_destroy(connect_task);
        task_retire_completed();
        closed_fd(connected_fd);
        /* An outstanding accept owns the listener after its wrapper drops. */
        accept_task = rt_async_accept_start(listener);
        rt_async_stream_close(listener);
        assert(fcntl(listener_fd, F_GETFD) >= 0);
        rt_task_destroy(accept_task);
        task_retire_completed();
        closed_fd(listener_fd);
        assert(rt_task_live_count() == 0);
    }
    return 0;
}
''')
    binary = tmp_path / 'ownership'
    compiled = subprocess.run(['cc', '-Wall', '-Wextra', str(source), '-o', str(binary)],
                              capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert (run.returncode, run.stderr) == (0, '')

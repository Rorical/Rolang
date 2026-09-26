import std.async_io
import std.task
import std.io

def writer(stream: AsyncStream) async -> i32 {
    await sleep(10);
    let written = await stream.write("hello");
    stream.shutdown_write();
    switch written {
        case .ok(let count): return count;
        case .err(let error): return -error;
    }
}

def main() async -> i32 {
    if let pair = AsyncPipe.create() {
        let sender = spawn writer(pair.second);
        while true {
            let result = await pair.first.read(4096);
            switch result {
                case .ok(let chunk):
                    if chunk.len() == 0 { break; }
                    print(chunk);
                case .err(let error): return error;
            }
        }
        let count = await sender;
        return 0;
    }
    return 1;
}

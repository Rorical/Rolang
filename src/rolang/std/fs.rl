// Standard library: file system I/O
import "string.rl"

pub extern "C" def rt_file_open_handle(path: String, mode: i32) -> RawPtr;
pub extern "C" def rt_file_close(file: RawPtr) -> Void;
pub extern "C" def rt_file_read(file: RawPtr, buf: RawPtr, size: i32) -> i32;
pub extern "C" def rt_file_write(file: RawPtr, buf: RawPtr, size: i32) -> i32;
pub extern "C" def rt_file_read_all_handle(file: RawPtr) -> RawPtr;
pub extern "C" def rt_file_read_line_handle(file: RawPtr) -> RawPtr;
pub extern "C" def rt_file_write_string(file: RawPtr, s: String) -> i32;
pub extern "C" def rt_file_seek(file: RawPtr, offset: i64, whence: i32) -> i32;
pub extern "C" def rt_file_tell(file: RawPtr) -> i64;
pub extern "C" def rt_file_flush(file: RawPtr) -> i32;
pub extern "C" def rt_file_eof(file: RawPtr) -> i32;

pub struct File {
    var handle: RawPtr;

    pub unsafe static def open(path: String, mode: i32) -> File? {
        unsafe {
            let h = rt_file_open_handle(path, mode);
            if (h as i64) == 0 { return nil; }
            return File { handle: h };
        }
    }

    pub def close() -> Void {
        unsafe { rt_file_close(self.handle); }
    }
    pub def read(buf: RawPtr, size: i32) -> i32 {
        unsafe { return rt_file_read(self.handle, buf, size); }
    }
    pub def write(buf: RawPtr, size: i32) -> i32 {
        unsafe { return rt_file_write(self.handle, buf, size); }
    }
    pub def read_all() -> String {
        unsafe { return String.from_handle(rt_file_read_all_handle(self.handle)); }
    }
    pub def read_line() -> String {
        unsafe { return String.from_handle(rt_file_read_line_handle(self.handle)); }
    }
    pub def seek(offset: i64, whence: i32) -> i32 {
        unsafe { return rt_file_seek(self.handle, offset, whence); }
    }
    pub def tell() -> i64 {
        unsafe { return rt_file_tell(self.handle); }
    }
    pub def flush() -> i32 {
        unsafe { return rt_file_flush(self.handle); }
    }
    pub def eof() -> i32 {
        unsafe { return rt_file_eof(self.handle); }
    }
}

pub def fs_open(path: String, mode: i32) -> RawPtr { unsafe { return rt_file_open_handle(path, mode); } }
pub def fs_close(file: RawPtr) -> Void { unsafe { rt_file_close(file); } }
pub def fs_read(file: RawPtr, buf: RawPtr, size: i32) -> i32 { unsafe { return rt_file_read(file, buf, size); } }
pub def fs_write(file: RawPtr, buf: RawPtr, size: i32) -> i32 { unsafe { return rt_file_write(file, buf, size); } }
pub def fs_read_all(file: RawPtr) -> String { unsafe { return String.from_handle(rt_file_read_all_handle(file)); } }
pub def fs_read_line(file: RawPtr) -> String { unsafe { return String.from_handle(rt_file_read_line_handle(file)); } }
pub def fs_write_str(file: RawPtr, s: String) -> i32 { unsafe { return rt_file_write_string(file, s); } }
pub def fs_seek(file: RawPtr, offset: i64, whence: i32) -> i32 { unsafe { return rt_file_seek(file, offset, whence); } }
pub def fs_tell(file: RawPtr) -> i64 { unsafe { return rt_file_tell(file); } }
pub def fs_flush(file: RawPtr) -> i32 { unsafe { return rt_file_flush(file); } }
pub def fs_eof(file: RawPtr) -> i32 { unsafe { return rt_file_eof(file); } }

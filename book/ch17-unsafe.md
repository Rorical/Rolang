# Chapter 17: Unsafe Code and C Interop

Most Rolang code is safe by default — the type system prevents out-of-bounds access, use-after-free, and type confusion. Some operations inherently require stepping outside these guarantees. The `unsafe` keyword marks those boundaries explicitly.

## What Requires `unsafe`

Three categories of operations require an `unsafe { }` block:

1. **`RawPtr` operations** — casting to or from a raw pointer
2. **`extern "C"` function calls** — calling C functions declared with `extern`
3. **User-declared `unsafe def`** — calling functions explicitly marked unsafe

Outside of `unsafe` blocks, the compiler rejects any of the above.

## Declaring C Functions

Use `extern "C" def` to declare a C function that the linker will resolve:

```rolang
extern "C" def malloc(size: i64) -> RawPtr;
extern "C" def free(ptr: RawPtr) -> Void;
extern "C" def memcpy(dst: RawPtr, src: RawPtr, n: i64) -> RawPtr;
extern "C" def strlen(s: RawPtr) -> i64;
```

These are declarations only — no body. The actual implementation comes from a linked C library.

## Calling C Functions

`extern "C"` calls must be inside an `unsafe` block:

```rolang
def alloc(n: i64) -> RawPtr {
    unsafe {
        return malloc(n);
    }
}

def dealloc(ptr: RawPtr) -> Void {
    unsafe {
        free(ptr);
    }
}
```

## `RawPtr`

`RawPtr` is an untyped pointer — the C `void*`. It has no type information, no bounds checking, and no automatic ARC management. Use it only at the boundary with C code:

```rolang
extern "C" def rt_io_read_bytes(buf: RawPtr, n: i32) -> i32;

def read_into(v: Vec<u8>) -> i32 {
    unsafe {
        return rt_io_read_bytes(v.raw_handle(), v.len());
    }
}
```

### Casting to and from RawPtr

Cast a Rolang value to `RawPtr` with `as`, and cast a `RawPtr` back to a Rolang type with `as`:

```rolang
let n: i32 = 42;
let ptr: RawPtr = unsafe { n as RawPtr };

// Cast back — caller is responsible for correctness
let recovered: i32 = unsafe { ptr as i32 };
```

These casts are only valid inside `unsafe` blocks.

## Declaring Unsafe Functions

Mark a function `unsafe def` when it requires callers to uphold invariants that the type system cannot check:

```rolang
unsafe def raw_read_i32(ptr: RawPtr) -> i32 {
    return ptr as i32;
}

// Calling it requires unsafe:
let val: i32 = unsafe { raw_read_i32(some_ptr) };
```

## Public Unsafe Functions

Mark with both `pub` and `unsafe`:

```rolang
pub unsafe def raw_handle_of(v: Vec<i32>) -> RawPtr {
    return v.handle;
}
```

## Unsafe Does Not Propagate into Closures

An `unsafe` block's permission does not leak into closures defined inside it:

```rolang
unsafe {
    let ptr = malloc(64);
    let f = (x: i32) -> i32 { x + 1 };   // f is NOT unsafe
    // f cannot call malloc or use RawPtr
}
```

If a closure needs unsafe operations, it must contain its own `unsafe { }` block.

## Interoperating with a C Library

Here is a complete pattern for wrapping a C library:

**C header (conceptual):**
```c
int db_open(const char* path);
int db_close(int handle);
int db_get(int handle, const char* key, char* out, int out_len);
```

**Rolang wrapper:**
```rolang
extern "C" def db_open(path: RawPtr) -> i32;
extern "C" def db_close(handle: i32) -> i32;
extern "C" def db_get(handle: i32, key: RawPtr, out: RawPtr, out_len: i32) -> i32;

struct Database {
    var handle: i32;

    static def open(path: String) -> Database {
        var h: i32;
        unsafe {
            // String to RawPtr for the C call (conceptual)
            h = db_open(path as RawPtr);
        }
        return Database { handle: h };
    }

    def close() -> Void {
        unsafe { db_close(self.handle); }
    }

    def __release__() -> Void {
        self.close();
    }
}
```

The safe public API (`Database.open`, `.close`) confines all `unsafe` operations inside the implementation. Callers never touch `RawPtr` directly.

## Safe Cast Restrictions

Outside `unsafe`, only these casts are allowed:

| From → To | Allowed |
|-----------|---------|
| Smaller int → larger int | Yes (implicit widening) |
| Int → int (narrowing) | Yes, with `as` |
| Int ↔ float | Yes, with `as` |
| Int ↔ Bool | Yes, with `as` |
| `T` → `T?` | Yes, with `as` |
| `T` → `T` (identity) | Yes |
| Struct ↔ int | **No** |
| Struct ↔ struct | **No** |
| Existential → concrete | **No** — use pattern matching |
| Any ↔ `RawPtr` | **Only in unsafe** |

## Summary

- `unsafe { }` blocks allow `RawPtr` operations, `extern "C"` calls, and calls to `unsafe def` functions
- `extern "C" def name(params) -> T` declares a C function; no body needed
- `RawPtr` is an untyped C pointer with no safety guarantees — confine it to thin wrapper layers
- `unsafe def` marks a function whose callers must uphold invariants the type system cannot verify
- Unsafe does not propagate into closures; each closure needing unsafe operations declares its own
- Wrap unsafe code in safe public APIs so callers never see raw pointers

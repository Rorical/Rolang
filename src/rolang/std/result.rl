// Standard library: Result<T, E> for error handling.
//
// Usage:
//     let r: Result<i32, String> = Result.ok(value: 42);
//     if is_ok(r) { ... }

pub enum Result<T, E> {
    case ok(value: T);
    case err(error: E);
}

// Check variant ------------------------------------------------------------

pub def is_ok<T, E>(r: Result<T, E>) -> Bool {
    switch r {
        case .ok(let v): return true;
        default: return false;
    }
}

pub def is_err<T, E>(r: Result<T, E>) -> Bool {
    switch r {
        case .err(let e): return true;
        default: return false;
    }
}

// Unwrap with a caller-provided default. A panicking `unwrap` is now
// available via `import "panic.rl"; let v = unwrap_or(r, x);` plus an
// explicit panic from the err arm; this helper just returns `default`.
pub def unwrap_or<T, E>(r: Result<T, E>, default: T) -> T {
    switch r {
        case .ok(let v): return v;
        default: return default;
    }
}

// Map ---------------------------------------------------------------------

pub def map<T, U, E>(r: Result<T, E>, f: (T) -> U) -> Result<U, E> {
    switch r {
        case .ok(let v): return Result<U, E>.ok(value: f(v));
        case .err(let e): return Result<U, E>.err(error: e);
    }
}

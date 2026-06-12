// cycle_churn — cyclic-garbage stress test (mirrors cycle_churn.rl).
// Safe Rust cannot drop a true Rc cycle without leaking, so this mirrors the
// C version: heap nodes linked through raw pointers into a real ring,
// traversed, then explicitly freed by walking the cycle once more.

struct Node {
    next: *mut Node,
    value: i64,
}

fn main() {
    const R: i64 = 150000;
    const K: i64 = 64;
    let mut total: i64 = 0;

    for r in 0..R {
        unsafe {
            // Build the ring: first -> ... -> last -> first.
            let first = Box::into_raw(Box::new(Node { next: std::ptr::null_mut(), value: r }));
            let mut prev = first;
            for i in 1..K {
                let n = Box::into_raw(Box::new(Node { next: std::ptr::null_mut(), value: r + i }));
                (*prev).next = n;
                prev = n;
            }
            (*prev).next = first; // close the cycle

            // Traverse exactly K steps, summing values.
            let mut cur = first;
            for _ in 0..K {
                total += (*cur).value;
                cur = (*cur).next;
            }

            // Free the ring (walk K nodes).
            let mut cur = first;
            for _ in 0..K {
                let nxt = (*cur).next;
                drop(Box::from_raw(cur));
                cur = nxt;
            }
        }
    }

    println!("{}", total);
}

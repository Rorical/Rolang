// cycle_churn — cyclic-garbage stress test.
//
// Adversarial-by-design for Rolang: each iteration builds a RING of K nodes
// (last.next = first, a true reference cycle), traverses it once, and drops
// it WITHOUT unlinking. Reference counting alone can never reclaim a cycle,
// so the entire 9.6M-node garbage stream lands on the backup cycle
// collector — work that tracing GCs (Go/Java/JS/Python's gc) absorb as
// ordinary garbage. This measures cycle-collection throughput.
//
// Node values: ring r holds r, r+1, ..., r+K-1, so
//   total = K*R*(R-1)/2 + R*K*(K-1)/2   (closed form, verifies the runs).

import "io.rl"

struct Node {
    var next: Node?
    var value: i64
}

def main() -> i32 {
    let rings: i64 = 150000;
    let k: i64 = 64;
    var total: i64 = 0;

    var r: i64 = 0;
    while r < rings {
        // Build the ring: first -> ... -> last -> first.
        let first = Node { next: nil, value: r };
        var prev = first;
        var i: i64 = 1;
        while i < k {
            let n = Node { next: nil, value: r + i };
            prev.next = n;
            prev = n;
            i = i + 1;
        }
        prev.next = first;  // close the cycle

        // Traverse exactly K steps, summing values.
        var cur = first;
        var step: i64 = 0;
        while step < k {
            total = total + cur.value;
            switch cur.next {
                case .Some(let nx): { cur = nx; }
                case nil: {}
            }
            step = step + 1;
        }
        r = r + 1;
        // Ring dropped here with the cycle intact — cyclic garbage.
    }

    println_i64(total);
    return 0;
}

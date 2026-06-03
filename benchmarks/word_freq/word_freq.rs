// word_freq — Dict<String,i64> stress test (mirrors word_freq.rl).
// V=2000 base-26 keys pre-built once; T=2,000,000 tokens drawn from a Knuth
// MMIX LCG drive an insert-or-increment into the language's native hash map.
// Checksum = sum(count * 2654435761) + T  (== 2654435761*T + T, distribution-
// independent, so it validates total token count and matches the C output).
use std::collections::HashMap;

// base-26, least-significant letter first: 0->"a", 25->"z", 26->"ba", ...
fn make_word_key(id: i64) -> String {
    let alphabet = b"abcdefghijklmnopqrstuvwxyz";
    let mut n = id;
    let mut result = String::new();
    loop {
        let digit = (n % 26) as usize;
        result.push(alphabet[digit] as char);
        n /= 26;
        if n == 0 {
            break;
        }
    }
    result
}

fn main() {
    let v: i64 = 2000;
    let t: i64 = 2000000;

    // Pre-build all V key strings once (matches the Rolang hot-loop layout).
    let keys: Vec<String> = (0..v).map(make_word_key).collect();

    // Borrow &str keys from `keys` so the hot loop hashes without allocating.
    let mut counts: HashMap<&str, i64> = HashMap::new();

    let mut state: i64 = 12345678901234567;
    for _ in 0..t {
        state = state
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        let w = (((state >> 33) & 0x7FFFFFFF) % v) as usize;
        *counts.entry(keys[w].as_str()).or_insert(0) += 1;
    }

    let mut checksum: i64 = 0;
    for c in counts.values() {
        checksum += c * 2654435761;
    }
    checksum += t;

    println!("{}", checksum);
}

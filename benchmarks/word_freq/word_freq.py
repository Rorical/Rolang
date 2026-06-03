# word_freq — Dict<String,i64> stress test (mirrors word_freq.rl).
# V=2000 base-26 keys pre-built once; T=2,000,000 tokens from a Knuth MMIX LCG
# drive an insert-or-increment into a Python dict. State is masked to 64 bits so
# its bit pattern matches C's int64 wrap. Checksum matches the C output
# (== 2654435761*T + T).


# base-26, least-significant letter first: 0->"a", 25->"z", 26->"ba", ...
def make_word_key(id):
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    n = id
    result = ""
    while True:
        digit = n % 26
        result += alphabet[digit]
        n //= 26
        if n == 0:
            break
    return result


def main():
    V = 2000
    T = 2000000
    MASK = (1 << 64) - 1

    # Pre-build all V key strings once.
    keys = [make_word_key(i) for i in range(V)]

    counts = {}
    state = 12345678901234567
    for _ in range(T):
        # Knuth MMIX LCG step, masked to 64 bits (matches C int64 wrap).
        state = (state * 6364136223846793005 + 1442695040888963407) & MASK
        w = ((state >> 33) & 0x7FFFFFFF) % V
        k = keys[w]
        counts[k] = counts.get(k, 0) + 1

    checksum = 0
    for c in counts.values():
        checksum += c * 2654435761
    checksum += T

    print(checksum)


if __name__ == "__main__":
    main()

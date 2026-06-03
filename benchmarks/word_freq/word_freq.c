/*
 * word_freq.c — Word-frequency benchmark: hash-map C implementation.
 *
 * Mirrors word_freq.rl's approach: build the SAME V=2000 base-26 string keys
 * once, then tally T=2,000,000 LCG-drawn tokens in a real open-addressing hash
 * map (FNV-1a-64, linear probe, string-content keys) — the same workload that
 * word_freq.rl runs through Rolang's Dict<String,i64>. (The previous version
 * used a flat count[V] array, an unfair shortcut C can take because the
 * vocabulary is bounded; this makes word_freq an apples-to-apples hash-map
 * comparison across languages.)
 *
 *   LCG : state = state * 6364136223846793005 + 1442695040888963407
 *   w   = ((state >> 33) & 0x7fffffff) % V
 *   key = base-26 encoding of w (least-significant letter first)
 *
 * Checksum (distribution-independent, == 2654435761*T + T):
 *   checksum = sum over all entries of (count * 2654435761) + T
 *
 * Prints a single i64 via printf("%lld\n", checksum) == 5308871524000000.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define V 2000
#define T 2000000LL

/* base-26, least-significant letter first: 0->"a", 25->"z", 26->"ab", ... */
static char* make_word_key(int64_t id) {
    char buf[16];
    int n = 0;
    int64_t x = id;
    for (;;) {
        buf[n++] = (char)('a' + (x % 26));
        x /= 26;
        if (x == 0) break;
    }
    char* s = (char*)malloc((size_t)n + 1);
    memcpy(s, buf, (size_t)n);
    s[n] = '\0';
    return s;
}

/* FNV-1a-64 over the key bytes (matches Rolang Dict's string hashing). */
static uint64_t fnv1a(const char* s, size_t len) {
    uint64_t h = 1469598103934665603ULL;
    for (size_t i = 0; i < len; i++) {
        h ^= (uint8_t)s[i];
        h *= 1099511628211ULL;
    }
    return h;
}

/* Open-addressing string -> int64 map (linear probe, power-of-two buckets). */
typedef struct {
    const char** keys;   /* NULL = empty slot */
    size_t*      keylens;
    int64_t*     vals;
    size_t       cap;    /* power of two */
    size_t       len;
} Map;

static void map_init(Map* m, size_t cap) {
    m->cap = cap;
    m->len = 0;
    m->keys = (const char**)calloc(cap, sizeof(char*));
    m->keylens = (size_t*)calloc(cap, sizeof(size_t));
    m->vals = (int64_t*)calloc(cap, sizeof(int64_t));
}

static void map_grow(Map* m) {
    Map n;
    map_init(&n, m->cap * 2);
    uint64_t mask = n.cap - 1;
    for (size_t j = 0; j < m->cap; j++) {
        if (m->keys[j] == NULL) continue;
        uint64_t i = fnv1a(m->keys[j], m->keylens[j]) & mask;
        while (n.keys[i] != NULL) i = (i + 1) & mask;
        n.keys[i] = m->keys[j];
        n.keylens[i] = m->keylens[j];
        n.vals[i] = m->vals[j];
        n.len++;
    }
    free(m->keys);
    free(m->keylens);
    free(m->vals);
    *m = n;
}

/* Insert-or-increment: counts[key]++ in a single hash + probe. */
static void map_inc(Map* m, const char* key, size_t klen) {
    if ((m->len + 1) * 4 >= m->cap * 3) {   /* load factor 0.75 */
        map_grow(m);
    }
    uint64_t mask = m->cap - 1;
    uint64_t i = fnv1a(key, klen) & mask;
    for (;;) {
        if (m->keys[i] == NULL) {
            m->keys[i] = key;
            m->keylens[i] = klen;
            m->vals[i] = 1;
            m->len++;
            return;
        }
        if (m->keylens[i] == klen && memcmp(m->keys[i], key, klen) == 0) {
            m->vals[i]++;
            return;
        }
        i = (i + 1) & mask;
    }
}

int main(void) {
    /* Pre-build all V key strings once (matches word_freq.rl's hot-loop layout). */
    char* keys[V];
    size_t klens[V];
    for (int64_t i = 0; i < V; i++) {
        keys[i] = make_word_key(i);
        klens[i] = strlen(keys[i]);
    }

    /* Start small (like Rolang's Dict.with_capacity(16)) and grow on demand. */
    Map counts;
    map_init(&counts, 16);

    int64_t state = (int64_t)12345678901234567LL;
    for (int64_t t = 0; t < T; t++) {
        state = state * (int64_t)6364136223846793005LL
                      + (int64_t)1442695040888963407LL;
        int64_t w = ((state >> 33) & (int64_t)2147483647LL) % V;
        map_inc(&counts, keys[w], klens[w]);
    }

    int64_t checksum = 0;
    for (size_t i = 0; i < counts.cap; i++) {
        if (counts.keys[i] != NULL) {
            checksum += counts.vals[i] * (int64_t)2654435761LL;
        }
    }
    checksum += T;

    printf("%lld\n", (long long)checksum);
    return 0;
}

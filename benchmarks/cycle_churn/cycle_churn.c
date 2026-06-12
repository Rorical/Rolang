// cycle_churn — cyclic-garbage stress test (mirrors cycle_churn.rl).
// C has no GC: nodes are malloc'd, linked into a true ring, traversed, and
// then explicitly freed by walking the cycle once more. The explicit free is
// C's natural shape — the comparison point is what automatic memory
// management (tracing GC vs refcount + cycle collector) costs the others.
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

typedef struct Node {
    struct Node *next;
    int64_t value;
} Node;

int main(void) {
    const int64_t R = 150000;
    const int64_t K = 64;
    int64_t total = 0;

    for (int64_t r = 0; r < R; r++) {
        // Build the ring: first -> ... -> last -> first.
        Node *first = (Node *)malloc(sizeof(Node));
        first->value = r;
        Node *prev = first;
        for (int64_t i = 1; i < K; i++) {
            Node *n = (Node *)malloc(sizeof(Node));
            n->value = r + i;
            prev->next = n;
            prev = n;
        }
        prev->next = first;  // close the cycle

        // Traverse exactly K steps, summing values.
        Node *cur = first;
        for (int64_t step = 0; step < K; step++) {
            total += cur->value;
            cur = cur->next;
        }

        // Free the ring (walk K nodes).
        cur = first;
        for (int64_t step = 0; step < K; step++) {
            Node *nxt = cur->next;
            free(cur);
            cur = nxt;
        }
    }

    printf("%lld\n", (long long)total);
    return 0;
}

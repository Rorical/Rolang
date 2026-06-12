// cycle_churn — cyclic-garbage stress test (mirrors cycle_churn.rl).
// Rings are dropped with the cycle intact; the JVM's tracing GC reclaims
// them as ordinary young-generation garbage.
public class CycleChurn {
    static final class Node {
        Node next;
        long value;

        Node(long value) {
            this.value = value;
        }
    }

    public static void main(String[] args) {
        final long R = 150000;
        final int K = 64;
        long total = 0;

        for (long r = 0; r < R; r++) {
            // Build the ring: first -> ... -> last -> first.
            Node first = new Node(r);
            Node prev = first;
            for (int i = 1; i < K; i++) {
                Node n = new Node(r + i);
                prev.next = n;
                prev = n;
            }
            prev.next = first; // close the cycle

            // Traverse exactly K steps, summing values.
            Node cur = first;
            for (int step = 0; step < K; step++) {
                total += cur.value;
                cur = cur.next;
            }
            // Ring dropped here with the cycle intact.
        }

        System.out.println(total);
    }
}

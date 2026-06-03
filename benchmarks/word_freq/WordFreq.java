// word_freq — Dict<String,i64> stress test (mirrors word_freq.rl).
// V=2000 base-26 keys pre-built once; T=2,000,000 tokens from a Knuth MMIX LCG
// drive an insert-or-increment into java.util.HashMap. Checksum matches the C
// output (== 2654435761*T + T).
import java.util.HashMap;

public class WordFreq {
    // base-26, least-significant letter first: 0->"a", 25->"z", 26->"ba", ...
    static String makeWordKey(long id) {
        String alphabet = "abcdefghijklmnopqrstuvwxyz";
        long n = id;
        StringBuilder result = new StringBuilder();
        while (true) {
            int digit = (int) (n % 26);
            result.append(alphabet.charAt(digit));
            n /= 26;
            if (n == 0) break;
        }
        return result.toString();
    }

    public static void main(String[] args) {
        long V = 2000;
        long T = 2000000;

        // Pre-build all V key strings once.
        String[] keys = new String[(int) V];
        for (int i = 0; i < V; i++) {
            keys[i] = makeWordKey(i);
        }

        HashMap<String, Long> counts = new HashMap<>();

        long state = 12345678901234567L;
        for (long t = 0; t < T; t++) {
            // Knuth MMIX LCG step (long overflow wraps in Java).
            state = state * 6364136223846793005L + 1442695040888963407L;
            int w = (int) (((state >> 33) & 0x7FFFFFFFL) % V);
            counts.merge(keys[w], 1L, Long::sum);
        }

        long checksum = 0;
        for (long c : counts.values()) {
            checksum += c * 2654435761L;
        }
        checksum += T;

        System.out.println(checksum);
    }
}

// binary_trees — allocation-churn benchmark (mirrors binary_trees.c / .rl).
// maxDepth=14; nodes are heap objects reclaimed by the JVM GC.
public class BinaryTrees {
    static final class Tree {
        Tree left, right;
    }

    static Tree make(int depth) {
        Tree t = new Tree();
        if (depth != 0) {
            t.left = make(depth - 1);
            t.right = make(depth - 1);
        }
        return t;
    }

    static long check(Tree t) {
        if (t == null) return 0;
        return 1 + check(t.left) + check(t.right);
    }

    public static void main(String[] args) {
        int minDepth = 4;
        int maxDepth = 14;
        long total = 0;

        // stretch tree
        int stretchDepth = maxDepth + 1;
        Tree stretch = make(stretchDepth);
        total += check(stretch);
        stretch = null;

        // long-lived tree
        Tree longLived = make(maxDepth);

        // iteration loop
        for (int depth = minDepth; depth <= maxDepth; depth += 2) {
            int exp = maxDepth - depth + minDepth;
            long iterations = 1L << exp;
            for (long i = 0; i < iterations; i++) {
                Tree t = make(depth);
                total += check(t);
            }
        }

        // long-lived tree check
        total += check(longLived);

        System.out.println(total);
    }
}

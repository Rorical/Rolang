#include <stdio.h>
#include <stdlib.h>

typedef struct Tree {
    struct Tree *left;
    struct Tree *right;
} Tree;

static Tree *make(int depth) {
    Tree *t = (Tree *)malloc(sizeof(Tree));
    if (depth == 0) {
        t->left = NULL;
        t->right = NULL;
    } else {
        t->left  = make(depth - 1);
        t->right = make(depth - 1);
    }
    return t;
}

static long long check(Tree *t) {
    if (t == NULL) return 0;
    return 1 + check(t->left) + check(t->right);
}

static void free_tree(Tree *t) {
    if (t == NULL) return;
    free_tree(t->left);
    free_tree(t->right);
    free(t);
}

int main(void) {
    int min_depth = 4;
    int max_depth = 14;
    long long total = 0;

    /* stretch tree */
    int stretch_depth = max_depth + 1;
    Tree *stretch = make(stretch_depth);
    total += check(stretch);
    free_tree(stretch);

    /* long-lived tree */
    Tree *long_lived = make(max_depth);

    /* iteration loop */
    for (int depth = min_depth; depth <= max_depth; depth += 2) {
        long long exp = max_depth - depth + min_depth;
        long long iterations = 1LL << exp;
        for (long long i = 0; i < iterations; i++) {
            Tree *t = make(depth);
            total += check(t);
            free_tree(t);
        }
    }

    /* long-lived tree check */
    total += check(long_lived);
    free_tree(long_lived);

    printf("%lld\n", total);
    return 0;
}

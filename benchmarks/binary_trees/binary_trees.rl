import "io.rl"

struct Tree {
    var left: Tree?
    var right: Tree?
}

def make(depth: i64) -> Tree {
    if depth == 0 {
        return Tree { left: nil, right: nil };
    }
    let l = make(depth - 1);
    let r = make(depth - 1);
    return Tree { left: l, right: r };
}

def check(tree: Tree) -> i64 {
    var sum: i64 = 1;
    switch tree.left {
        case .Some(let l): {
            sum = sum + check(l);
        }
        case nil: {}
    }
    switch tree.right {
        case .Some(let r): {
            sum = sum + check(r);
        }
        case nil: {}
    }
    return sum;
}

def main() -> i32 {
    let min_depth: i64 = 4;
    let max_depth: i64 = 14;
    var total: i64 = 0;

    // stretch tree
    let stretch_depth = max_depth + 1;
    let stretch = make(stretch_depth);
    total = total + check(stretch);

    // long-lived tree
    let long_lived = make(max_depth);

    // iteration loop: depth = minDepth, minDepth+2, ..., maxDepth
    var depth: i64 = min_depth;
    while depth <= max_depth {
        // iterations = 2^(max_depth - depth + min_depth)
        let exp: i64 = max_depth - depth + min_depth;
        var iterations: i64 = 1;
        var e: i64 = 0;
        while e < exp {
            iterations = iterations * 2;
            e = e + 1;
        }
        var i: i64 = 0;
        while i < iterations {
            let t = make(depth);
            total = total + check(t);
            i = i + 1;
        }
        depth = depth + 2;
    }

    // long-lived tree check
    total = total + check(long_lived);

    println_i64(total);
    return 0;
}

// binary_trees — allocation-churn benchmark (mirrors binary_trees.c / .rl).
// maxDepth=14; builds, checks and drops many short-lived trees plus one
// long-lived tree. Box<Tree> gives malloc/free-style ownership (RAII drop),
// the Rust analogue of C's malloc/free and Rolang's ARC.

struct Tree {
    left: Option<Box<Tree>>,
    right: Option<Box<Tree>>,
}

fn make(depth: i32) -> Box<Tree> {
    if depth == 0 {
        Box::new(Tree { left: None, right: None })
    } else {
        Box::new(Tree {
            left: Some(make(depth - 1)),
            right: Some(make(depth - 1)),
        })
    }
}

fn check(t: &Tree) -> i64 {
    let mut sum: i64 = 1;
    if let Some(ref l) = t.left {
        sum += check(l);
    }
    if let Some(ref r) = t.right {
        sum += check(r);
    }
    sum
}

fn main() {
    let min_depth: i32 = 4;
    let max_depth: i32 = 14;
    let mut total: i64 = 0;

    // stretch tree
    let stretch_depth = max_depth + 1;
    let stretch = make(stretch_depth);
    total += check(&stretch);
    drop(stretch);

    // long-lived tree
    let long_lived = make(max_depth);

    // iteration loop
    let mut depth = min_depth;
    while depth <= max_depth {
        let exp = max_depth - depth + min_depth;
        let iterations: i64 = 1i64 << exp;
        for _ in 0..iterations {
            let t = make(depth);
            total += check(&t);
        }
        depth += 2;
    }

    // long-lived tree check
    total += check(&long_lived);

    println!("{}", total);
}

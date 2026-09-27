enum Tree<T> {
    case leaf(T); case branch(Tree<T>, Tree<T>);
    def first() -> T { return switch self { case .leaf(let value): value; case .branch(let left, _): left.first(); }; }
}
def identity<T>(value: T) -> T { return value; }
def choose<T>(flag: Bool, a: T, b: T) -> T { return switch flag { case true: a; case false: b; }; }
def main() -> i32 {
    let a = Tree<i32>.leaf(40);
    let b: Tree<i32> = Tree.branch(a, Tree.leaf(2));
    let word = Tree<String>.leaf("ok");
    if !identity(word.first()).equals("ok") { return 1; }
    return identity(choose(true, b.first(), 0)) + 2;
}

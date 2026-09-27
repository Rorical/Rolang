// Shared references and methods in the native bootstrap compiler.
struct Counter {
    pub var value: i32;

    pub static def new(value: i32) -> Counter {
        return Counter { value: value };
    }
    pub def increment() -> Void { self.value = self.value + 1; }
    pub def read() -> i32 { return self.value; }
}

def main() -> i32 {
    let counter = Counter.new(40);
    let alias = counter;
    alias.increment();
    counter.increment();
    return alias.read();
}

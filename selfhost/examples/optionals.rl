// A compiler-style lookup: a missing binding is distinct from a zero value.
struct Binding { let name: String; let slot: i32; }

def lookup(bindings: Vec<Binding>, name: String) -> Binding? {
    for binding in bindings {
        if binding.name.equals(name) { return binding; }
    }
    return nil;
}

def main() -> i32 {
    let bindings = Vec<Binding>.new();
    bindings.push(Binding { name: "zero", slot: 0 });
    bindings.push(Binding { name: "answer", slot: 42 });
    if let binding = lookup(bindings, "missing") { return 1; }
    if let binding = lookup(bindings, "zero") {
        if binding.slot != 0 { return 2; }
    } else { return 3; }
    if let binding = lookup(bindings, "answer") { return binding.slot; }
    return 4;
}

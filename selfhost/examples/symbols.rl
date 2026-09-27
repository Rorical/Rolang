// Dictionary-backed nested compiler scopes; exits with status 42.
struct Binding { var number: i32; }
def lookup(scopes: Vec<Dict<String, Binding>>, name: String) -> Binding? {
    var i = scopes.len() - 1;
    while i >= 0 { if let binding = scopes[i].get(name) { return binding; } i = i - 1; }
    return nil;
}
def main() -> i32 {
    let scopes = Vec<Dict<String, Binding>>.new();
    let outer = Dict<String, Binding>.with_capacity(8, 1);
    let inner = Dict<String, Binding>.with_capacity(8, 1);
    outer.set("x", Binding { number: 5 }); inner.set("x", Binding { number: 20 });
    scopes.push(outer); scopes.push(inner);
    if let binding = lookup(scopes, "x") { binding.number = 42; } else { return 1; }
    let snapshot = inner.values(); inner.clear();
    if snapshot[0].number != 42 { return 2; }
    if let binding = lookup(scopes, "x") { if binding.number != 5 { return 3; } } else { return 4; }
    if let binding = lookup(scopes, "missing") { return 5; }
    return snapshot[0].number;
}

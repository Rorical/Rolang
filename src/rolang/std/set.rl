// Standard library: generic Set<T>
//
// Backed by Dict<T, i32> with all values set to 1. The dict already
// understands fixed-size key types and string keys (via key_kind=1), so
// the set automatically inherits the same key compatibility.
//
// Insertion order is NOT preserved. `iter()` walks elements in the
// underlying dict's internal bucket order.

import "dict.rl"
import "vec.rl"
import "string.rl"

pub struct Set<T> {
    var d: Dict<T, i32>;

    pub static def new(capacity: i32, key_kind: i32, key_type_id: i32) -> Set<T> {
        var s: Set<T>;
        let d: Dict<T, i32> = Dict<T, i32>.with_capacity(capacity, key_kind);
        s.d = d;
        return s;
    }

    // dict.set is an upsert, so we don't return a "was new?" signal;
    // callers needing it should `contains` first.
    pub def add(elem: T) -> Void {
        self.d.set(elem, 1);
    }

    pub def contains(elem: T) -> Bool {
        return self.d.contains(elem);
    }

    pub def len() -> i64 {
        return self.d.len();
    }

    pub def is_empty() -> Bool {
        return self.d.len() == 0;
    }

    pub def free() -> Void {
        self.d.free();
    }
}

// ---- convenience constructors ----
//
// Each constructor goes through a typed local for the Dict instance so
// Rolang's generic inference can pin down `<K, V>` from the variable's
// annotation. Assigning the dict_new result directly to the `s.d`
// field does NOT currently propagate the field's type constraint into
// the call, so we route through an intermediate.

pub def set_string_new() -> Set<String> {
    return Set<String>.new(16, 1, 0);
}

pub def set_i32_new() -> Set<i32> {
    return Set<i32>.new(16, 0, 0);
}

pub def set_i64_new() -> Set<i64> {
    return Set<i64>.new(16, 0, 0);
}

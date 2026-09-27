// Rolang's optional type is T?. Helpers compose it without forced unwrapping.
pub def option_map<T, U>(value: T?, transform: (T) -> U) -> U? {
    if let item = value { return transform(item); }
    return nil;
}

pub def option_and_then<T, U>(value: T?, transform: (T) -> U?) -> U? {
    if let item = value { return transform(item); }
    return nil;
}

pub def option_filter<T>(value: T?, predicate: (T) -> Bool) -> T? {
    if let item = value { if predicate(item) { return item; } }
    return nil;
}

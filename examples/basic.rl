// Basic RoLang Example
// A Swift-like, statically typed, ARC-based systems language

// Struct definition with properties
struct Point {
    var x: i32;
    var y: i32;

    def distance_squared() -> i64 {
        let dx = self.x * self.x;
        let dy = self.y * self.y;
        return (dx + dy) as i64;
    }
}

// Struct with __release__ (Python-style dunder destructor)
struct File {
    var fd: i32;

    static def open(path: String) -> File {
        return File { fd: 0 };
    }

    def __release__() -> Void {
        // close(fd)
    }
}

// Generic enum - Option type
enum Option<T> {
    case none
    case some(T)
}

// Generic enum with labeled payload - Result type
enum Result<T, E> {
    case ok(value: T)
    case err(error: E)
}

// Protocol (trait) definition
protocol Show {
    def show() -> String;
}

protocol Drawable {
    var width: i32 { get };
    var height: i32 { get set };
}

// Generic function
def identity<T>(x: T) -> T {
    return x;
}

// Function with protocol constraint
def print_show<T: Show>(item: T) {
    let s = item.show();
}

// Extension to add methods to existing types
extension Point {
    def magnitude() -> f64 {
        return 0.0;
    }
}

// External C function declaration
extern "C" def printf(fmt: RawPtr) -> i32;

// Main function demonstrating various features
def main() -> i32 {
    // Variable declarations
    let x: i32 = 42;
    var y = 10;

    // Arithmetic expressions
    let sum = x + y;
    let product = x * y;
    let complex = 1 + 2 * 3 - 4 / 2;

    // Boolean expressions
    let is_positive = x > 0;
    let both = x > 0 && y > 0;
    let either = x > 0 || y < 0;

    // Conditionals
    if sum > 50 {
        y = y + 1;
    } else {
        y = y - 1;
    }

    // Nested if-else
    if x > 100 {
        y = 100;
    } else if x > 50 {
        y = 50;
    } else {
        y = 0;
    }

    // While loop
    while y > 0 {
        y = y - 1;
    }

    // For loop with array literal
    for i in [1, 2, 3] {
        let squared = i * i;
    }

    // Pattern matching with switch
    let opt: Option<i32> = Option.some(42);
    switch opt {
    case .none:
        let zero = 0;
    case .some(let v):
        let value = v;
    }

    // Multiple patterns in one case
    switch x {
    case 0:
        y = 0;
    case 1:
        y = 1;
    default:
        y = -1;
    }

    // v2: heap-allocated types use ARC automatically
    let point = Point { x: 1, y: 2 };
    point.x = 10;

    // Optional type annotation
    let maybe: Point? = nil;

    // Ternary expression
    let result = x > 0 ? 1 : 0;

    // Nil coalescing
    let value = maybe ?? Point { x: 0, y: 0 };

    // Array and dictionary literals (sugar for Vec<T> / Dict<K, V>).
    let numbers = [1, 2, 3, 4, 5];
    let dict = ["a": 1, "b": 2];
    // An empty literal carries no element types, so build the empty
    // collection through the explicit constructor.
    let empty_dict = Dict<String, i32>.with_capacity(0, 1);

    // Member access
    let px = point.x;

    // Method call
    let dist = point.distance_squared();

    // Subscript access
    let first = numbers[0];

    // Lambda expressions
    let add = { a: i32, b: i32 in
        return a + b;
    };

    // Tuple expressions
    let pair = (1, 2);
    let named = (x: 10, y: 20);

    // Defer statement for cleanup
    defer {
        y = 0;
    }

    return 0;
}

// Async function (syntax support)
def fetch() async -> Result<String, String> {
    return Result.ok(value: "data");
}

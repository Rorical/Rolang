// ---------------------------------------------------------------------------
// JSON Parser & Recursive Pretty-Printer
//
// Demonstrates Rolang's expressive type system and compiler capabilities:
//   - Generic enums with labeled payloads (JsonValue AST)
//   - Structs with methods (Parser state machine)
//   - Pattern-matching switch (print_json dispatch)
//   - Generic collections  (Vec<JsonValue>, Dict<String, JsonValue>)
//   - Character-level string iteration (char_at, len, substring)
//   - Short-circuit logic operators (&&, ||)
//   - Iterator-protocol dict key iteration (for k in dict_keys(d))
//   - Recursive descent parsing
//   - 2-space indented pretty-printing
// ---------------------------------------------------------------------------

import "vec.rl"
import "dict.rl"
import "string.rl"
import "io.rl"
import "char.rl"
import "iter.rl"

// ============================================================================
// JSON AST
// ============================================================================

enum JsonValue {
    case null_val;
    case bool_val(val: Bool);
    case int_val(val: i64);
    case str_val(val: String);
    case arr_val(elements: Vec<JsonValue>);
    case obj_val(fields: Dict<String, JsonValue>);
}

// ============================================================================
// JSON Parser — recursive descent with character-by-character lexing.
//
// Characters are identified by ASCII codes:
//   '"' = 34    ',' = 44    '-' = 45    '0'–'9' = 48–57
//   ':' = 58    '[' = 91    ']' = 93    '{' = 123   '}' = 125
//   'n' = 110   't' = 116   'f' = 102   ' '=32  '\t'=9  '\n'=10  '\r'=13
// ============================================================================

struct Parser {
    var src: String;
    var pos: i32;

    // -- primitive character operations ----------------------------------

    pub def peek() -> i32 { self.src.char_at(self.pos) }
    pub def advance() -> Void { self.pos = self.pos + 1; }
    pub def eof() -> Bool { self.pos as i64 >= self.src.len() }

    pub def skip_ws() -> Void {
        while !self.eof() {
            let c = self.peek();
            if char_is_space(c) { self.advance(); }
            else { break; }
        }
    }

    pub def expect(ch: i32) -> Bool {
        if self.peek() == ch { self.advance(); return true; }
        return false;
    }

    // -- value readers ---------------------------------------------------

    pub def is_digit(ch: i32) -> Bool { char_is_digit(ch) }

    pub def read_int() -> i64 {
        var sign: i64 = 1;
        if self.peek() == 45 { self.advance(); sign = -1; }
        var val: i64 = 0;
        while !self.eof() && self.is_digit(self.peek()) {
            val = val * 10 + ((self.peek() - 48) as i64);
            self.advance();
        }
        sign * val
    }

    pub def read_string() -> String {
        if !self.expect(34) { return ""; }
        var start = self.pos;
        while !self.eof() {
            if self.peek() == 34 { break; }
            self.advance();
        }
        let end = self.pos;
        self.advance();
        self.src.substring(start, end - start)
    }

    // -- recursive descent -----------------------------------------------

    pub def parse() -> JsonValue { self.parse_value() }

    pub def parse_value() -> JsonValue {
        self.skip_ws();
        if self.eof() { return JsonValue.null_val; }

        // null
        if self.peek() == 110 {
            self.advance(); self.advance(); self.advance(); self.advance();
            return JsonValue.null_val;
        }
        // true
        if self.peek() == 116 {
            self.advance(); self.advance(); self.advance(); self.advance();
            return JsonValue.bool_val(true);
        }
        // false
        if self.peek() == 102 {
            self.advance(); self.advance(); self.advance(); self.advance(); self.advance();
            return JsonValue.bool_val(false);
        }
        // string
        if self.peek() == 34 {
            return JsonValue.str_val(self.read_string());
        }
        // number  (leading digit or '-')
        let c = self.peek();
        if c == 45 || self.is_digit(c) {
            return JsonValue.int_val(self.read_int());
        }
        // array
        if self.peek() == 91 {
            return self.parse_array();
        }
        // object
        if self.peek() == 123 {
            return self.parse_object();
        }
        JsonValue.null_val
    }

    pub def parse_array() -> JsonValue {
        self.advance(); self.skip_ws();
        // empty array
        if self.peek() == 93 { self.advance(); return JsonValue.arr_val(vec_json_new()); }

        var v = vec_json_new();
        v.push(self.parse_value());
        while true {
            self.skip_ws();
            if self.peek() == 44 { self.advance(); v.push(self.parse_value()); }
            else { break; }
        }
        self.expect(93);
        JsonValue.arr_val(v)
    }

    pub def parse_object() -> JsonValue {
        self.advance(); self.skip_ws();
        // empty object
        if self.peek() == 125 { self.advance(); return JsonValue.obj_val(dict_json_new()); }

        var d = dict_json_new();
        while true {
            self.skip_ws();
            let key = self.read_string();
            self.skip_ws(); self.expect(58);
            d.set(key, self.parse_value());
            self.skip_ws();
            if self.peek() == 44 { self.advance(); } else { break; }
        }
        self.expect(125);
        JsonValue.obj_val(d)
    }
}

// ============================================================================
// Typed Collection Constructors
// (elem_type_id = 0 since type_id_of(T) is not yet a source-level built-in)
// ============================================================================

def vec_json_new() -> Vec<JsonValue> {
    return Vec<JsonValue>.new();
}

def dict_json_new() -> Dict<String, JsonValue> {
    return Dict<String, JsonValue>.with_capacity(16, 1);
}

// ============================================================================
// Recursive Pretty-Printer
//
// Prints JsonValue with 2-space indentation.  Demonstrates:
//   - switch with pattern matching on enum variants
//   - `for k in dict_keys(d)` iteration over a Dict<String, _>
// ============================================================================

def spaces(n: i32) -> String {
    "  ".repeat(n)
}

def print_json(val: JsonValue, indent: i32) -> Void {
    switch val {
        case .null_val:
            println("null");

        case .bool_val(let v):
            if v { println("true"); }
            else { println("false"); }

        case .int_val(let v):
            println_i64(v);

        case .str_val(let v):
            print("\"");
            print(v);
            println("\"");

        case .arr_val(let v):
            let len = v.len();
            if len == 0 {
                println("[]");
            } else {
                println("[");
                var i: i32 = 0;
                while i < len {
                    print(spaces(indent + 1));
                    print_json(v.get(i), indent + 1);
                    i = i + 1;
                }
                print(spaces(indent));
                println("]");
            }

        case .obj_val(let v):
            let len = v.len() as i32;
            if len == 0 {
                println("{}");
            } else {
                println("{");
                for key in dict_keys(v) {
                    let val2 = read_dict_val(v, key);
                    print(spaces(indent + 1));
                    print("\"");
                    print(key);
                    print("\": ");
                    print_json(val2, indent + 1);
                }
                print(spaces(indent));
                println("}");
            }

        default:
            println("<?>");
    }
}

/// Look up a value in the dict by key.
/// Uses x as RawPtr to obtain the address of the local out slot, which
/// rt_dict_get then fills with the stored JsonValue pointer.
def read_dict_val(d: Dict<String, JsonValue>, key: String) -> JsonValue {
    return d.get(key) ?? JsonValue.null_val;
}

// ============================================================================
// Main
// ============================================================================

def main() -> i32 {
    let src = "{\"name\": \"Rolang\", \"version\": 1, \"tags\": [\"compiler\", \"json\"], \"nested\": {\"active\": true, \"count\": 0}}";
    var p = Parser { src: src, pos: 0 };
    let val = p.parse();
    print_json(val, 0);
    return 0;
}

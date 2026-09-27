import std.string_builder

// Typed slots avoid pointer/integer aliasing; lifetime follows the bootstrap arena.
pub def emit_vector_runtime(output: StringBuilder) -> Void {
    output.append_line("typedef struct { int64_t number; void *reference; } rl_slot;");
    output.append_line("struct rl_vec { int32_t length, capacity; rl_slot *data; };");
    output.append_line("static void rl_vec_bounds(rl_vec *v, int32_t i) { if (!v || i < 0 || i >= v->length) { fputs(\"Vec index out of bounds\\n\", stderr); exit(1); } }");
    output.append_line("static void rl_vec_resize(rl_vec *v, int32_t capacity) {");
    output.append_line("    if (capacity <= v->capacity) return;");
    output.append_line("    if ((uint64_t)capacity > SIZE_MAX / sizeof(rl_slot)) { fputs(\"Vec capacity too large\\n\", stderr); exit(1); }");
    output.append_line("    rl_slot *data = rl_allocate((size_t)capacity * sizeof(rl_slot));");
    output.append_line("    if (v->length) memcpy(data, v->data, (size_t)v->length * sizeof(rl_slot));");
    output.append_line("    v->data = data; v->capacity = capacity;");
    output.append_line("}");
    output.append_line("static rl_vec *rl_vec_new(int32_t capacity) { rl_vec *v = rl_allocate(sizeof(*v)); rl_vec_resize(v, capacity < 4 ? 4 : capacity); return v; }");
    output.append_line("static void rl_vec_push(rl_vec *v, rl_slot value) {");
    output.append_line("    if (v->length == INT32_MAX) { fputs(\"Vec capacity exceeds INT32_MAX\\n\", stderr); exit(1); }");
    output.append_line("    if (v->length == v->capacity) rl_vec_resize(v, v->capacity >= INT32_MAX / 2 ? INT32_MAX : v->capacity * 2);");
    output.append_line("    v->data[v->length++] = value;");
    output.append_line("}");
    output.append_line("static rl_slot rl_vec_get(rl_vec *v, int32_t i) { rl_vec_bounds(v, i); return v->data[i]; }");
    output.append_line("static void rl_vec_set(rl_vec *v, int32_t i, rl_slot value) { rl_vec_bounds(v, i); v->data[i] = value; }");
    output.append_line("static rl_slot rl_vec_pop(rl_vec *v) {");
    output.append_line("    if (!v->length) return (rl_slot){0};");
    output.append_line("    rl_slot value = v->data[--v->length]; v->data[v->length] = (rl_slot){0}; return value;");
    output.append_line("}");
}

import std.string_builder

// Typed slots avoid pointer/integer aliasing; lifetime follows the bootstrap arena.
pub def emit_vector_runtime(output: StringBuilder) -> Void {
    output.append(r"""typedef struct { int64_t number; void *reference; } rl_slot;
struct rl_vec { int32_t length, capacity; rl_slot *data; };
static void rl_vec_bounds(rl_vec *v, int32_t i) { if (!v || i < 0 || i >= v->length) { fputs("Vec index out of bounds\n", stderr); exit(1); } }
static void rl_vec_resize(rl_vec *v, int32_t capacity) {
    if (capacity <= v->capacity) return;
    if ((uint64_t)capacity > SIZE_MAX / sizeof(rl_slot)) { fputs("Vec capacity too large\n", stderr); exit(1); }
    rl_slot *data = rl_allocate((size_t)capacity * sizeof(rl_slot));
    if (v->length) memcpy(data, v->data, (size_t)v->length * sizeof(rl_slot));
    v->data = data; v->capacity = capacity;
}
static rl_vec *rl_vec_new(int32_t capacity) { rl_vec *v = rl_allocate(sizeof(*v)); rl_vec_resize(v, capacity < 4 ? 4 : capacity); return v; }
static void rl_vec_push(rl_vec *v, rl_slot value) {
    if (v->length == INT32_MAX) { fputs("Vec capacity exceeds INT32_MAX\n", stderr); exit(1); }
    if (v->length == v->capacity) rl_vec_resize(v, v->capacity >= INT32_MAX / 2 ? INT32_MAX : v->capacity * 2);
    v->data[v->length++] = value;
}
static rl_slot rl_vec_get(rl_vec *v, int32_t i) { rl_vec_bounds(v, i); return v->data[i]; }
static void rl_vec_set(rl_vec *v, int32_t i, rl_slot value) { rl_vec_bounds(v, i); v->data[i] = value; }
static rl_slot rl_vec_pop(rl_vec *v) {
    if (!v->length) return (rl_slot){0};
    rl_slot value = v->data[--v->length]; v->data[v->length] = (rl_slot){0}; return value;
}
""");
}

import std.string_builder

pub def emit_range_runtime(output: StringBuilder) -> Void {
    output.append(r"""struct rl_range { int32_t start, end, inclusive; };
static rl_range *rl_range_new(int32_t start, int32_t end, int32_t inclusive) {
    rl_range *r = rl_allocate(sizeof(*r));
    r->start = start; r->end = end; r->inclusive = inclusive; return r;
}
static int64_t rl_range_lower(rl_range *r, int64_t length) {
    return r->start < 0 ? 0 : (r->start > length ? length : r->start);
}
static int64_t rl_range_upper(rl_range *r, int64_t length) {
    int64_t end = (int64_t)r->end + r->inclusive;
    return end < 0 ? 0 : (end > length ? length : end);
}
static rl_vec *rl_vec_slice(rl_vec *v, rl_range *r) {
    int64_t start = rl_range_lower(r, v->length), end = rl_range_upper(r, v->length);
    rl_vec *out = rl_vec_new(end > start ? (int32_t)(end - start) : 0);
    for (int64_t i = start; i < end; ++i) rl_vec_push(out, rl_vec_get(v, (int32_t)i));
    return out;
}
static rl_string *rl_string_slice(rl_string *s, rl_range *r) {
    int64_t start = rl_range_lower(r, s->length), end = rl_range_upper(r, s->length);
    return rl_string_new(s->data + start, end > start ? end - start : 0);
}
""");
}

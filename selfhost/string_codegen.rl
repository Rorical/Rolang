import std.string_builder

pub def emit_string_runtime(output: StringBuilder) -> Void {
    output.append(r"""struct rl_string { const unsigned char *data; int64_t length; };
static rl_string *rl_string_new(const unsigned char *data, int64_t length) {
    rl_string *s = rl_allocate(sizeof(*s));
    unsigned char *bytes = rl_allocate((size_t)length + 1);
    if (length) memcpy(bytes, data, (size_t)length);
    s->data = bytes; s->length = length; return s;
}
static int32_t rl_string_compare(rl_string *a, rl_string *b) {
    int64_t n = a->length < b->length ? a->length : b->length;
    int cmp = memcmp(a->data, b->data, (size_t)n);
    if (cmp) return cmp < 0 ? -1 : 1;
    return (a->length > b->length) - (a->length < b->length);
}
static rl_string *rl_string_concat(rl_string *a, rl_string *b) {
    if (b->length > INT64_MAX - a->length || (uint64_t)(a->length + b->length) >= SIZE_MAX) { fputs("string too large\n", stderr); exit(1); }
    rl_string *s = rl_allocate(sizeof(*s)); s->length = a->length + b->length;
    unsigned char *bytes = rl_allocate((size_t)s->length + 1);
    memcpy(bytes, a->data, (size_t)a->length); memcpy(bytes + a->length, b->data, (size_t)b->length);
    s->data = bytes; return s;
}
static int32_t rl_string_contains(rl_string *a, rl_string *b) {
    if (!b->length) return 1;
    for (int64_t i = 0; i <= a->length - b->length; ++i) if (!memcmp(a->data + i, b->data, (size_t)b->length)) return 1;
    return 0;
}
static int32_t rl_string_starts_with(rl_string *a, rl_string *b) { return b->length <= a->length && !memcmp(a->data, b->data, (size_t)b->length); }
static int32_t rl_string_ends_with(rl_string *a, rl_string *b) { return b->length <= a->length && !memcmp(a->data + a->length - b->length, b->data, (size_t)b->length); }
static int32_t rl_string_byte(rl_string *s, int32_t i) { return i < 0 || i >= s->length ? -1 : s->data[i]; }
static int32_t rl_string_find_char(rl_string *s, int32_t c, int32_t start) {
    for (int64_t i = start < 0 ? 0 : start; i < s->length; ++i) if (s->data[i] == c) return rl_bits((uint32_t)i);
    return -1;
}
static rl_string *rl_string_substring(rl_string *s, int32_t start, int32_t length) {
    if (start < 0) start = 0;
    if (start >= s->length || length <= 0) return rl_string_new((const unsigned char *)"", 0);
    int64_t n = length; if (n > s->length - start) n = s->length - start;
    return rl_string_new(s->data + start, n);
}
static rl_string *rl_integer_string(int64_t value) { char buf[32]; int n = snprintf(buf, sizeof(buf), "%lld", (long long)value); return rl_string_new((const unsigned char *)buf, n); }
static int64_t rl_bits64(uint64_t x) { int64_t y; memcpy(&y, &x, 8); return y; }
static int64_t rl_add64(int64_t a,int64_t b) { return rl_bits64((uint64_t)a+(uint64_t)b); }
static int64_t rl_sub64(int64_t a,int64_t b) { return rl_bits64((uint64_t)a-(uint64_t)b); }
static int64_t rl_mul64(int64_t a,int64_t b) { return rl_bits64((uint64_t)a*(uint64_t)b); }
static int64_t rl_neg64(int64_t a) { return rl_bits64(0u-(uint64_t)a); }
static int64_t rl_div64(int64_t a,int64_t b) { if(!b) rl_zero(); if(a==INT64_MIN && b==-1) return INT64_MIN; return a/b; }
static int64_t rl_rem64(int64_t a,int64_t b) { if(!b) rl_zero(); if(a==INT64_MIN && b==-1) return 0; return a%b; }
""");
}

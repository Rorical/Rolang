import std.string_builder

// Ordered entries with a linear-probe hash index; allocations follow the bootstrap arena.
pub def emit_dict_runtime(output: StringBuilder) -> Void {
    output.append(r"""struct rl_dict { rl_vec *keys, *values; uint32_t *buckets; size_t capacity; int kind; };
static uint64_t rl_dict_hash(rl_dict *d, rl_slot key) {
    uint64_t h;
    if (d->kind == 1) {
        rl_string *s = key.reference; h = UINT64_C(14695981039346656037);
        if (s) for (int64_t i = 0; i < s->length; ++i) { h ^= s->data[i]; h *= UINT64_C(1099511628211); }
        return h;
    }
    h = d->kind == 2 ? (uint64_t)(uintptr_t)key.reference : (uint64_t)key.number;
    h ^= h >> 30; h *= UINT64_C(0xbf58476d1ce4e5b9); h ^= h >> 27;
    h *= UINT64_C(0x94d049bb133111eb); return h ^ (h >> 31);
}
static int rl_dict_equal(rl_dict *d, rl_slot a, rl_slot b) {
    if (d->kind == 1) {
        if (a.reference == b.reference) return 1;
        if (!a.reference || !b.reference) return 0;
        return rl_string_compare(a.reference, b.reference) == 0;
    }
    return d->kind == 2 ? a.reference == b.reference : a.number == b.number;
}
static size_t rl_dict_bucket(rl_dict *d, rl_slot key) {
    size_t p = (size_t)rl_dict_hash(d, key) & (d->capacity - 1);
    while (d->buckets[p] && !rl_dict_equal(d, d->keys->data[d->buckets[p] - 1], key)) p = (p + 1) & (d->capacity - 1);
    return p;
}
static void rl_dict_rebuild(rl_dict *d) {
    memset(d->buckets, 0, d->capacity * sizeof(*d->buckets));
    for (int32_t i = 0; i < d->keys->length; ++i) d->buckets[rl_dict_bucket(d, d->keys->data[i])] = (uint32_t)i + 1;
}
static void rl_dict_capacity(rl_dict *d, uint64_t entries) {
    if (entries > INT32_MAX) { fputs("Dict capacity exceeds INT32_MAX\n", stderr); exit(1); }
    uint64_t capacity = 16;
    while (capacity < entries * 2) capacity *= 2;
    if (capacity <= d->capacity) return;
    if (capacity > SIZE_MAX / sizeof(*d->buckets)) { fputs("Dict capacity too large\n", stderr); exit(1); }
    d->buckets = rl_allocate((size_t)capacity * sizeof(*d->buckets)); d->capacity = (size_t)capacity;
    rl_dict_rebuild(d);
}
static rl_dict *rl_dict_new(int32_t capacity, int32_t key_kind, int32_t reference, int32_t string) {
    if (capacity < 0) { fputs("negative Dict capacity\n", stderr); exit(1); }
    if (key_kind == 1 && !string) { fputs("Dict string key kind requires String keys\n", stderr); exit(1); }
    rl_dict *d = rl_allocate(sizeof(*d)); d->kind = key_kind == 1 ? 1 : reference ? 2 : 0;
    d->keys = rl_vec_new(capacity); d->values = rl_vec_new(capacity); rl_dict_capacity(d, (uint64_t)capacity); return d;
}
static int64_t rl_dict_find(rl_dict *d, rl_slot key) { return (int64_t)d->buckets[rl_dict_bucket(d, key)] - 1; }
static int64_t rl_dict_entry(rl_dict *d, rl_slot key, rl_slot value) {
    int64_t index = rl_dict_find(d, key); if (index >= 0) return index;
    rl_dict_capacity(d, (uint64_t)d->keys->length + 1);
    index = d->keys->length;
    size_t bucket = rl_dict_bucket(d, key);
    rl_vec_push(d->keys, key); rl_vec_push(d->values, value); d->buckets[bucket] = (uint32_t)index + 1; return index;
}
static void rl_dict_set(rl_dict *d, rl_slot key, rl_slot value) { int64_t i = rl_dict_entry(d, key, value); d->values->data[i] = value; }
static rl_optional *rl_dict_get(rl_dict *d, rl_slot key) { int64_t i = rl_dict_find(d, key); return i < 0 ? NULL : rl_some(d->values->data[i]); }
static rl_optional *rl_dict_remove(rl_dict *d, rl_slot key) {
    int64_t i = rl_dict_find(d, key); if (i < 0) return NULL;
    rl_optional *value = rl_some(d->values->data[i]);
    size_t count = (size_t)(d->keys->length - i - 1);
    memmove(d->keys->data + i, d->keys->data + i + 1, count * sizeof(rl_slot));
    memmove(d->values->data + i, d->values->data + i + 1, count * sizeof(rl_slot));
    d->keys->data[--d->keys->length] = (rl_slot){0}; d->values->data[--d->values->length] = (rl_slot){0};
    rl_dict_rebuild(d); return value;
}
static void rl_dict_clear(rl_dict *d) {
    memset(d->keys->data, 0, (size_t)d->keys->length * sizeof(rl_slot));
    memset(d->values->data, 0, (size_t)d->values->length * sizeof(rl_slot));
    d->keys->length = d->values->length = 0; rl_dict_rebuild(d);
}
static rl_vec *rl_dict_snapshot(rl_vec *source) {
    rl_vec *result = rl_vec_new(source->length); result->length = source->length;
    if (source->length) memcpy(result->data, source->data, (size_t)source->length * sizeof(rl_slot));
    return result;
}
static rl_slot rl_dict_value_at(rl_dict *d, int64_t i) { return i < 0 || i >= d->values->length ? (rl_slot){0} : d->values->data[i]; }
static void rl_dict_set_at(rl_dict *d, int64_t i, rl_slot value) { if (i >= 0 && i < d->values->length) d->values->data[i] = value; }
""");
}

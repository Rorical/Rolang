import std.string_builder

// Byte-oriented builder; snapshots own their bytes and allocations use the arena.
pub def emit_builder_runtime(output: StringBuilder) -> Void {
    output.append_line("struct rl_builder { unsigned char *data; int64_t length; size_t capacity; };");
    output.append_line("static rl_builder *rl_builder_new(void) { return rl_allocate(sizeof(rl_builder)); }");
    output.append_line("static void rl_builder_reserve(rl_builder *b, int64_t extra) {");
    output.append_line("    if (extra < 0 || extra > INT64_MAX - b->length || (uint64_t)(b->length + extra) >= SIZE_MAX) { fputs(\"StringBuilder too large\\n\", stderr); exit(1); }");
    output.append_line("    size_t need = (size_t)(b->length + extra) + 1;");
    output.append_line("    if (need <= b->capacity) return;");
    output.append_line("    size_t capacity = b->capacity ? b->capacity : 32;");
    output.append_line("    while (capacity < need) { if (capacity > SIZE_MAX / 2) { capacity = need; break; } capacity *= 2; }");
    output.append_line("    unsigned char *data = rl_allocate(capacity);");
    output.append_line("    if (b->length) memcpy(data, b->data, (size_t)b->length);");
    output.append_line("    b->data = data; b->capacity = capacity;");
    output.append_line("}");
    output.append_line("static void rl_builder_append(rl_builder *b, rl_string *s) {");
    output.append_line("    rl_builder_reserve(b, s->length);");
    output.append_line("    if (s->length) memcpy(b->data + b->length, s->data, (size_t)s->length);");
    output.append_line("    b->length += s->length; b->data[b->length] = 0;");
    output.append_line("}");
    output.append_line("static void rl_builder_byte(rl_builder *b, uint8_t value) { rl_builder_reserve(b, 1); b->data[b->length++] = value; b->data[b->length] = 0; }");
    output.append_line("static void rl_builder_line(rl_builder *b, rl_string *s) { rl_builder_append(b, s); rl_builder_byte(b, 10); }");
    output.append_line("static void rl_builder_clear(rl_builder *b) { b->length = 0; if (b->data) b->data[0] = 0; }");
    output.append_line("static rl_string *rl_builder_text(rl_builder *b) { return rl_string_new(b->data, b->length); }");
}

import std.string_builder

pub def native_stdlib_module(name: String) -> String {
    if name.equals("argc") || name.equals("argv") { return "std.process"; }
    if name.equals("print") || name.equals("println") || name.equals("print_i32") || name.equals("println_i32") || name.equals("println_i64") { return "std.io"; }
    if name.equals("fs_open") || name.equals("fs_close") || name.equals("fs_read_all") || name.equals("fs_read_line") || name.equals("fs_write_str") || name.equals("fs_flush") || name.equals("fs_seek") || name.equals("fs_tell") || name.equals("fs_eof") { return "std.fs"; }
    if name.equals("path_join") || name.equals("path_dirname") || name.equals("path_basename") || name.equals("path_extension") || name.equals("path_exists") || name.equals("path_is_dir") || name.equals("path_is_file") || name.equals("path_resolve") { return "std.path"; }
    return "";
}

// Minimal standard-library bridge needed by the native compiler CLI.
pub def emit_os_runtime(output: StringBuilder) -> Void {
    output.append_line("static int32_t rl_argc; static char **rl_argv;");
    output.append_line("static rl_string *rl_argument(int32_t index) {");
    output.append_line("    if (index < 0 || index >= rl_argc) return rl_string_new(NULL, 0);");
    output.append_line("    return rl_string_new((const unsigned char *)rl_argv[index], (int64_t)strlen(rl_argv[index]));");
    output.append_line("}");
    output.append_line("static void rl_print(rl_string *s) { if (s->length) fwrite(s->data, 1, (size_t)s->length, stdout); }");
    output.append_line("static void rl_println(rl_string *s) { rl_print(s); fputc('\\n', stdout); }");
    output.append_line("static int rl_path_valid(rl_string *p) { return p->length > 0 && !memchr(p->data, 0, (size_t)p->length); }");
    output.append_line("static void *rl_file_open(rl_string *path, int32_t mode) { return rl_path_valid(path) ? fopen((const char *)path->data, mode == 1 ? \"wb\" : mode == 2 ? \"ab\" : \"rb\") : NULL; }");
    output.append_line("static void rl_file_close(void *file) { if (file) fclose(file); }");
    output.append_line("static rl_string *rl_file_text(void *file, int line) {");
    output.append_line("    rl_builder *b = rl_builder_new();");
    output.append_line("    if (file) {");
    output.append_line("        if (line) { int c; while ((c = fgetc(file)) != EOF) { rl_builder_byte(b, (uint8_t)c); if (c == '\\n') break; } }");
    output.append_line("        else {");
    output.append_line("            unsigned char buffer[4096]; size_t n;");
    output.append_line("            while ((n = fread(buffer, 1, sizeof(buffer), file)) != 0) {");
    output.append_line("                rl_builder_reserve(b, (int64_t)n); memcpy(b->data + b->length, buffer, n); b->length += (int64_t)n; b->data[b->length] = 0;");
    output.append_line("            }");
    output.append_line("        }");
    output.append_line("    }");
    output.append_line("    return rl_builder_text(b);");
    output.append_line("}");
    output.append_line("static rl_string *rl_file_read_all(void *file) { return rl_file_text(file, 0); }");
    output.append_line("static rl_string *rl_file_read_line(void *file) { return rl_file_text(file, 1); }");
    output.append_line("static int32_t rl_file_write(void *file, rl_string *s) { if (!file || s->length > INT32_MAX) return 0; return (int32_t)fwrite(s->data, 1, (size_t)s->length, file); }");
    output.append_line("static int32_t rl_file_flush(void *file) { return file ? fflush(file) : -1; }");
    output.append_line("static int32_t rl_file_seek(void *file, int64_t offset, int32_t whence) { return file && offset >= LONG_MIN && offset <= LONG_MAX ? fseek(file, (long)offset, whence) : -1; }");
    output.append_line("static int64_t rl_file_tell(void *file) { return file ? (int64_t)ftell(file) : -1; }");
    output.append_line("static int32_t rl_file_eof(void *file) { return file ? feof(file) : 1; }");
    output.append_line("static rl_string *rl_path_join(rl_string *a, rl_string *b) {");
    output.append_line("    if (!a->length || (b->length && b->data[0] == '/')) return rl_string_new(b->data, b->length);");
    output.append_line("    if (!b->length) return rl_string_new(a->data, a->length);");
    output.append_line("    if (a->data[a->length - 1] == '/') return rl_string_concat(a, b);");
    output.append_line("    return rl_string_concat(rl_string_concat(a, rl_string_new((const unsigned char *)\"/\", 1)), b);");
    output.append_line("}");
    output.append_line("static rl_string *rl_path_dirname(rl_string *p) {");
    output.append_line("    int64_t end = p->length; while (end > 1 && p->data[end - 1] == '/') --end;");
    output.append_line("    int64_t slash = end - 1; while (slash >= 0 && p->data[slash] != '/') --slash;");
    output.append_line("    if (slash < 0) return rl_string_new((const unsigned char *)\".\", 1);");
    output.append_line("    return rl_string_new(p->data, slash ? slash : 1);");
    output.append_line("}");
    output.append_line("static rl_string *rl_path_basename(rl_string *p) {");
    output.append_line("    int64_t end = p->length; while (end > 1 && p->data[end - 1] == '/') --end;");
    output.append_line("    int64_t slash = end - 1; while (slash >= 0 && p->data[slash] != '/') --slash;");
    output.append_line("    return rl_string_new(p->data + slash + 1, end - slash - 1);");
    output.append_line("}");
    output.append_line("static rl_string *rl_path_extension(rl_string *p) {");
    output.append_line("    int64_t dot = -1, slash = -1;");
    output.append_line("    for (int64_t i = p->length - 1; i >= 0; --i) { if (p->data[i] == '/') { slash = i; break; } if (p->data[i] == '.' && dot < 0) dot = i; }");
    output.append_line("    return dot <= slash + 1 ? rl_string_new(NULL, 0) : rl_string_new(p->data + dot + 1, p->length - dot - 1);");
    output.append_line("}");
    output.append_line("static int32_t rl_path_stat(rl_string *p, int kind) {");
    output.append_line("    struct stat st; if (!rl_path_valid(p) || stat((const char *)p->data, &st) != 0) return 0;");
    output.append_line("    return kind == 1 ? S_ISDIR(st.st_mode) : kind == 2 ? S_ISREG(st.st_mode) : 1;");
    output.append_line("}");
    output.append_line("static int32_t rl_path_exists(rl_string *p) { return rl_path_stat(p, 0); }");
    output.append_line("static int32_t rl_path_is_dir(rl_string *p) { return rl_path_stat(p, 1); }");
    output.append_line("static int32_t rl_path_is_file(rl_string *p) { return rl_path_stat(p, 2); }");
    output.append_line("static rl_string *rl_path_resolve(rl_string *p) {");
    output.append_line("#if defined(__linux__) || defined(__APPLE__)");
    output.append_line("    if (rl_path_valid(p)) {");
    output.append_line("        char *resolved = realpath((const char *)p->data, NULL);");
    output.append_line("        if (resolved) { rl_string *s = rl_string_new((const unsigned char *)resolved, (int64_t)strlen(resolved)); free(resolved); return s; }");
    output.append_line("    }");
    output.append_line("#endif");
    output.append_line("    return rl_string_new(p->data, p->length);");
    output.append_line("}");
}

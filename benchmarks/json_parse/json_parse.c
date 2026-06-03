#include <stdio.h>
#include <string.h>

static const char *src = "{\"users\":[{\"id\":1,\"name\":\"Alice\",\"active\":true,\"scores\":[85,92,78]},{\"id\":2,\"name\":\"Bob\",\"active\":false,\"scores\":[91,88,95]},{\"id\":3,\"name\":\"Charlie\",\"active\":true,\"scores\":[76,84,90]}],\"metadata\":{\"version\":2,\"generated\":false,\"tags\":[\"benchmark\",\"json\",\"test\"],\"config\":{\"timeout\":30,\"retries\":3}}}";

typedef struct { const char *restrict src; int pos; int len; } Parser;

static int peek(Parser *p) { return p->src[p->pos]; }
static void advance(Parser *p) { p->pos++; }
static int eof(Parser *p) { return p->pos >= p->len; }
static int is_digit(int c) { return c >= '0' && c <= '9'; }
static int is_space(int c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; }

static void skip_ws(Parser *p) {
    while (!eof(p) && is_space(peek(p))) advance(p);
}

static long long parse_string(Parser *p) {
    advance(p);
    long long len = 0;
    while (peek(p) != '"') {
        advance(p);
        len++;
    }
    advance(p);
    return len;
}

static long long parse_number(Parser *p) {
    long long sign = 1;
    if (peek(p) == '-') { advance(p); sign = -1; }
    long long val = 0;
    while (!eof(p) && is_digit(peek(p))) {
        val = val * 10 + (peek(p) - '0');
        advance(p);
    }
    return sign * val;
}

static long long parse_value(Parser *p);

static long long parse_array(Parser *p) {
    advance(p); skip_ws(p);
    if (peek(p) == ']') { advance(p); return 0; }
    long long sum = 0;
    for (;;) {
        sum += parse_value(p);
        skip_ws(p);
        if (peek(p) == ',') advance(p);
        else break;
    }
    advance(p);
    return sum;
}

static long long parse_object(Parser *p) {
    advance(p); skip_ws(p);
    if (peek(p) == '}') { advance(p); return 0; }
    long long sum = 0;
    for (;;) {
        skip_ws(p);
        parse_string(p);  /* discard key */
        skip_ws(p);
        advance(p);  /* ':' */
        sum += parse_value(p);
        skip_ws(p);
        if (peek(p) == ',') advance(p);
        else break;
    }
    advance(p);
    return sum;
}

static long long parse_value(Parser *p) {
    skip_ws(p);
    if (eof(p)) return 0;
    int c = peek(p);
    if (c == '"') return parse_string(p);
    if (c == 't') { advance(p); advance(p); advance(p); advance(p); return 1; }
    if (c == 'f') { advance(p); advance(p); advance(p); advance(p); advance(p); return 0; }
    if (c == 'n') { advance(p); advance(p); advance(p); advance(p); return 0; }
    if (c == '-' || is_digit(c)) return parse_number(p);
    if (c == '[') return parse_array(p);
    if (c == '{') return parse_object(p);
    return 0;
}

int main(void) {
    Parser parser = { .src = src, .pos = 0, .len = (int)strlen(src) };
    long long total = 0;
    for (int i = 0; i < 100000; i++) {
        parser.pos = 0;
        total += parse_value(&parser);
    }
    printf("%lld\n", total);
    return 0;
}

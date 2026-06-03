public class JsonParse {
    static final String SRC = "{\"users\":[{\"id\":1,\"name\":\"Alice\",\"active\":true,\"scores\":[85,92,78]},{\"id\":2,\"name\":\"Bob\",\"active\":false,\"scores\":[91,88,95]},{\"id\":3,\"name\":\"Charlie\",\"active\":true,\"scores\":[76,84,90]}],\"metadata\":{\"version\":2,\"generated\":false,\"tags\":[\"benchmark\",\"json\",\"test\"],\"config\":{\"timeout\":30,\"retries\":3}}}";

    static class Parser {
        String src;
        int pos;

        Parser(String src) { this.src = src; }

        char peek() { return src.charAt(pos); }
        void advance() { pos++; }
        boolean eof() { return pos >= src.length(); }
        static boolean isDigit(int c) { return c >= '0' && c <= '9'; }
        static boolean isSpace(int c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; }

        void skipWS() {
            while (!eof() && isSpace(peek())) advance();
        }

        long parseString() {
            advance();
            long n = 0;
            while (peek() != '"') { advance(); n++; }
            advance();
            return n;
        }

        long parseNumber() {
            long sign = 1;
            if (peek() == '-') { advance(); sign = -1; }
            long val = 0;
            while (!eof() && isDigit(peek())) {
                val = val * 10 + (peek() - '0');
                advance();
            }
            return sign * val;
        }

        long parseValue() {
            skipWS();
            if (eof()) return 0;
            int c = peek();
            if (c == '"') return parseString();
            if (c == 't') { advance(); advance(); advance(); advance(); return 1; }
            if (c == 'f') { advance(); advance(); advance(); advance(); advance(); return 0; }
            if (c == 'n') { advance(); advance(); advance(); advance(); return 0; }
            if (c == '-' || isDigit(c)) return parseNumber();
            if (c == '[') return parseArray();
            if (c == '{') return parseObject();
            return 0;
        }

        long parseArray() {
            advance(); skipWS();
            if (peek() == ']') { advance(); return 0; }
            long sum = 0;
            while (true) {
                sum += parseValue();
                skipWS();
                if (peek() == ',') advance();
                else break;
            }
            advance();
            return sum;
        }

        long parseObject() {
            advance(); skipWS();
            if (peek() == '}') { advance(); return 0; }
            long sum = 0;
            while (true) {
                skipWS();
                parseString();
                skipWS();
                advance();
                sum += parseValue();
                skipWS();
                if (peek() == ',') advance();
                else break;
            }
            advance();
            return sum;
        }
    }

    public static void main(String[] args) {
        Parser parser = new Parser(SRC);
        long total = 0;
        for (int i = 0; i < 100000; i++) {
            parser.pos = 0;
            total += parser.parseValue();
        }
        System.out.println(total);
    }
}

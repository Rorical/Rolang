const SRC = '{"users":[{"id":1,"name":"Alice","active":true,"scores":[85,92,78]},{"id":2,"name":"Bob","active":false,"scores":[91,88,95]},{"id":3,"name":"Charlie","active":true,"scores":[76,84,90]}],"metadata":{"version":2,"generated":false,"tags":["benchmark","json","test"],"config":{"timeout":30,"retries":3}}}';

function Parser(src) {
    this.src = src;
    this.pos = 0;
}

Parser.prototype.peek = function() { return this.src.charCodeAt(this.pos); };
Parser.prototype.advance = function() { this.pos++; };
Parser.prototype.eof = function() { return this.pos >= this.src.length; };

Parser.prototype.isDigit = function(c) { return c >= 48 && c <= 57; };
Parser.prototype.isSpace = function(c) { return c === 32 || c === 9 || c === 10 || c === 13; };

Parser.prototype.skipWS = function() {
    while (!this.eof() && this.isSpace(this.peek())) this.advance();
};

Parser.prototype.parseString = function() {
    this.advance();
    var n = 0;
    while (this.peek() !== 34) { this.advance(); n++; }
    this.advance();
    return n;
};

Parser.prototype.parseNumber = function() {
    var sign = 1;
    if (this.peek() === 45) { this.advance(); sign = -1; }
    var val = 0;
    while (!this.eof() && this.isDigit(this.peek())) {
        val = val * 10 + (this.peek() - 48);
        this.advance();
    }
    return sign * val;
};

Parser.prototype.parseValue = function() {
    this.skipWS();
    if (this.eof()) return 0;
    var c = this.peek();
    if (c === 34) return this.parseString();
    if (c === 116) { this.advance(); this.advance(); this.advance(); this.advance(); return 1; }
    if (c === 102) { this.advance(); this.advance(); this.advance(); this.advance(); this.advance(); return 0; }
    if (c === 110) { this.advance(); this.advance(); this.advance(); this.advance(); return 0; }
    if (c === 45 || this.isDigit(c)) return this.parseNumber();
    if (c === 91) return this.parseArray();
    if (c === 123) return this.parseObject();
    return 0;
};

Parser.prototype.parseArray = function() {
    this.advance(); this.skipWS();
    if (this.peek() === 93) { this.advance(); return 0; }
    var sum = 0;
    while (true) {
        sum += this.parseValue();
        this.skipWS();
        if (this.peek() === 44) this.advance();
        else break;
    }
    this.advance();
    return sum;
};

Parser.prototype.parseObject = function() {
    this.advance(); this.skipWS();
    if (this.peek() === 125) { this.advance(); return 0; }
    var sum = 0;
    while (true) {
        this.skipWS();
        this.parseString();
        this.skipWS();
        this.advance();
        sum += this.parseValue();
        this.skipWS();
        if (this.peek() === 44) this.advance();
        else break;
    }
    this.advance();
    return sum;
};

var parser = new Parser(SRC);
var total = 0;
for (var i = 0; i < 100000; i++) {
    parser.pos = 0;
    total += parser.parseValue();
}
console.log(total);

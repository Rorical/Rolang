package main

import "fmt"

const src = `{"users":[{"id":1,"name":"Alice","active":true,"scores":[85,92,78]},{"id":2,"name":"Bob","active":false,"scores":[91,88,95]},{"id":3,"name":"Charlie","active":true,"scores":[76,84,90]}],"metadata":{"version":2,"generated":false,"tags":["benchmark","json","test"],"config":{"timeout":30,"retries":3}}}`

type Parser struct {
	src string
	pos int
}

func (p *Parser) peek() byte  { return p.src[p.pos] }
func (p *Parser) advance()     { p.pos++ }
func (p *Parser) eof() bool    { return p.pos >= len(p.src) }
func isDigit(c byte) bool      { return c >= '0' && c <= '9' }
func isSpace(c byte) bool      { return c == ' ' || c == '\t' || c == '\n' || c == '\r' }

func (p *Parser) skipWS() {
	for !p.eof() && isSpace(p.peek()) {
		p.advance()
	}
}

func (p *Parser) parseString() int64 {
	p.advance()
	var n int64 = 0
	for p.peek() != '"' {
		p.advance()
		n++
	}
	p.advance()
	return n
}

func (p *Parser) parseNumber() int64 {
	sign := int64(1)
	if p.peek() == '-' {
		p.advance()
		sign = -1
	}
	var val int64 = 0
	for !p.eof() && isDigit(p.peek()) {
		val = val*10 + int64(p.peek()-'0')
		p.advance()
	}
	return sign * val
}

func (p *Parser) parseValue() int64 {
	p.skipWS()
	if p.eof() {
		return 0
	}
	c := p.peek()
	switch {
	case c == '"':
		return p.parseString()
	case c == 't':
		p.advance()
		p.advance()
		p.advance()
		p.advance()
		return 1
	case c == 'f':
		p.advance()
		p.advance()
		p.advance()
		p.advance()
		p.advance()
		return 0
	case c == 'n':
		p.advance()
		p.advance()
		p.advance()
		p.advance()
		return 0
	case c == '-' || isDigit(c):
		return p.parseNumber()
	case c == '[':
		return p.parseArray()
	case c == '{':
		return p.parseObject()
	}
	return 0
}

func (p *Parser) parseArray() int64 {
	p.advance()
	p.skipWS()
	if p.peek() == ']' {
		p.advance()
		return 0
	}
	var sum int64 = 0
	for {
		sum += p.parseValue()
		p.skipWS()
		if p.peek() == ',' {
			p.advance()
		} else {
			break
		}
	}
	p.advance()
	return sum
}

func (p *Parser) parseObject() int64 {
	p.advance()
	p.skipWS()
	if p.peek() == '}' {
		p.advance()
		return 0
	}
	var sum int64 = 0
	for {
		p.skipWS()
		p.parseString()
		p.skipWS()
		p.advance()
		sum += p.parseValue()
		p.skipWS()
		if p.peek() == ',' {
			p.advance()
		} else {
			break
		}
	}
	p.advance()
	return sum
}

func main() {
	parser := Parser{src: src, pos: 0}
	var total int64 = 0
	for i := 0; i < 100000; i++ {
		parser.pos = 0
		total += parser.parseValue()
	}
	fmt.Println(total)
}

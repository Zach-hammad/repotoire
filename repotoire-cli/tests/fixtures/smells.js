// smells.js — Intentionally bad JavaScript code for integration testing
// This file contains multiple code smells and security issues that
// Repotoire detectors should flag.

const express = require('express');
const cors = require('cors');

// ============================================================================
// Express app without helmet or rate limiting
// ============================================================================

const app = express();

// CORS misconfiguration: wildcard origin
app.use(cors({ origin: '*' }));
// Also set the header directly
app.set('Access-Control-Allow-Origin', '*');

app.get('/api/users', (req, res) => {
    res.json({ users: [] });
});

app.post('/api/users', (req, res) => {
    res.json({ created: true });
});

app.get('/api/posts', (req, res) => {
    res.json({ posts: [] });
});

app.put('/api/posts/:id', (req, res) => {
    res.json({ updated: true });
});

app.delete('/api/posts/:id', (req, res) => {
    res.json({ deleted: true });
});

app.get('/api/comments', (req, res) => {
    res.json({ comments: [] });
});

// ============================================================================
// Insecure random used for security token generation
// ============================================================================

function generateSessionToken() {
    // Math.random() is not cryptographically secure
    const token = Math.random().toString(36).substring(2);
    return token;
}

function generateAuthToken(userId) {
    const secret = Math.random().toString(16).slice(2);
    return userId + ':' + secret;
}

// ============================================================================
// Empty catch blocks
// ============================================================================

function fetchUserData(id) {
    try { return JSON.parse(getData(id)); } catch (e) {}
    return null;
}

function saveConfig(data) {
    try { writeFile('/etc/config', data); } catch (err) { }
    return false;
}

// ============================================================================
// Deep nesting (5+ levels)
// ============================================================================

function processOrder(order) {
    if (order) {
        if (order.items) {
            for (let i = 0; i < order.items.length; i++) {
                if (order.items[i].price > 0) {
                    if (order.items[i].quantity > 0) {
                        if (order.items[i].inStock) {
                            console.log('Processing item:', order.items[i].name);
                            return order.items[i].price * order.items[i].quantity;
                        }
                    }
                }
            }
        }
    }
    return 0;
}

function validateInput(data) {
    if (data !== null) {
        if (typeof data === 'object') {
            if (data.hasOwnProperty('name')) {
                if (data.name.length > 0) {
                    if (data.name.length < 255) {
                        if (/^[a-zA-Z]+$/.test(data.name)) {
                            return true;
                        }
                    }
                }
            }
        }
    }
    return false;
}

// ============================================================================
// Magic numbers
// ============================================================================

function calculateShipping(weight) {
    if (weight < 5) {
        return weight * 3.99;
    } else if (weight < 20) {
        return weight * 2.49 + 7.50;
    } else {
        return weight * 1.75 + 15.00 + 9999;
    }
}

function applyDiscount(price, loyaltyLevel) {
    if (loyaltyLevel === 1) {
        return price * 0.95;
    } else if (loyaltyLevel === 2) {
        return price * 0.85;
    } else if (loyaltyLevel === 3) {
        return price * 0.70;
    }
    return price;
}

function getTimeout() {
    return 86400000;
}

// ============================================================================
// Debug / console.log statements left in production code
// ============================================================================

function handleRequest(req) {
    console.log('Incoming request:', req.url);
    console.log('Headers:', req.headers);
    const result = processRequest(req);
    console.log('Result:', result);
    console.debug('Debug info:', { timestamp: Date.now() });
    return result;
}

function updateUser(user) {
    console.log('Updating user:', user.id);
    console.log('Old data:', user);
    user.updatedAt = new Date();
    console.log('New data:', user);
    return user;
}

// ============================================================================
// ReDoS vulnerable regex patterns
// ============================================================================

function validateEmail(input) {
    // Evil regex with nested quantifiers — catastrophic backtracking
    const emailRegex = new RegExp('(a+)+b');
    return emailRegex.test(input);
}

function matchPattern(text) {
    const pattern = /(a|a)+$/;
    return pattern.test(text);
}

function checkInput(str) {
    // Nested quantifier ReDoS
    const re = /([a-zA-Z]+)*@/;
    return re.test(str);
}

// ============================================================================
// Additional issues for density
// ============================================================================

function riskyOperation() {
    try { eval('dangerous code'); } catch (e) {}
}

function moreDebugging() {
    console.log('checkpoint 1');
    console.log('checkpoint 2');
    console.log('checkpoint 3');
}

app.listen(3000);

module.exports = { app, generateSessionToken, generateAuthToken };

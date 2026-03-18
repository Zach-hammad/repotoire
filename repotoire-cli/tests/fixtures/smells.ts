/**
 * TypeScript fixture with intentional code smells and security issues.
 * Used by integration tests to verify detectors fire on TypeScript code.
 */

// ============================================================================
// 1. Empty try/catch blocks (single-line form for detector matching)
// ============================================================================

function riskyOperation(data: string): string {
    try { return JSON.parse(data); } catch (e) {}
    return "";
}

function anotherRiskyOp(input: string): string {
    try { return atob(input); } catch (err) { }
    return "";
}

function thirdRisky(val: string): number {
    try { return parseInt(val); } catch (e) {}
    return 0;
}

// ============================================================================
// 2. Deep nesting (5+ levels)
// ============================================================================

function processOrder(order: any): string {
    if (order) {
        if (order.items) {
            if (order.items.length > 0) {
                if (order.customer) {
                    if (order.customer.verified) {
                        if (order.customer.balance > 0) {
                            return "processed";
                        }
                    }
                }
            }
        }
    }
    return "failed";
}

function validateInput(data: any): boolean {
    if (data !== null) {
        if (typeof data === "object") {
            if (data.hasOwnProperty("name")) {
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
// 3. Magic numbers
// ============================================================================

function calculateShipping(weight: number): number {
    if (weight < 5) {
        return weight * 3.99;
    } else if (weight < 20) {
        return weight * 2.49 + 7.50;
    } else {
        return weight * 1.75 + 15.00 + 9999;
    }
}

function applyDiscount(price: number, loyaltyLevel: number): number {
    if (loyaltyLevel === 1) {
        return price * 0.95;
    } else if (loyaltyLevel === 2) {
        return price * 0.85;
    } else if (loyaltyLevel === 3) {
        return price * 0.70;
    }
    return price;
}

function getTimeout(): number {
    return 86400000;
}

function retryWithBackoff(attempt: number): number {
    return Math.min(attempt * 1500, 30000);
}

// ============================================================================
// 4. console.log debug statements (many to exceed density threshold)
// ============================================================================

function handleRequest(req: any): any {
    console.log("Incoming request:", req.url);
    console.log("Headers:", req.headers);
    const result = processRequest(req);
    console.log("Result:", result);
    console.debug("Debug info:", { timestamp: Date.now() });
    return result;
}

function updateUser(user: any): any {
    console.log("Updating user:", user.id);
    console.log("Old data:", user);
    user.updatedAt = new Date();
    console.log("New data:", user);
    return user;
}

function moreDebugging(): void {
    console.log("checkpoint 1");
    console.log("checkpoint 2");
    console.log("checkpoint 3");
}

declare function processRequest(req: any): any;

// ============================================================================
// 5. Callback hell (nested callbacks)
// ============================================================================

function loadDashboard(userId: string): void {
    getUser(userId, function(user: any) {
        getOrders(user.id, function(orders: any) {
            getOrderDetails(orders[0].id, function(details: any) {
                getShipping(details.shippingId, function(shipping: any) {
                    renderDashboard(user, orders, details, shipping, function() {
                        console.log("Dashboard loaded");
                    });
                });
            });
        });
    });
}

declare function getUser(id: string, cb: Function): void;
declare function getOrders(id: string, cb: Function): void;
declare function getOrderDetails(id: string, cb: Function): void;
declare function getShipping(id: string, cb: Function): void;
declare function renderDashboard(a: any, b: any, c: any, d: any, cb: Function): void;

// ============================================================================
// 6. Implicit coercion (== instead of ===)
// ============================================================================

function checkValue(x: any): string {
    if (x == null) {
        return "null";
    }
    if (x == 0) {
        return "zero";
    }
    if (x == "") {
        return "empty";
    }
    if (x == false) {
        return "falsy";
    }
    return "other";
}

// ============================================================================
// 7. Missing .catch() on promises
// ============================================================================

function fireAndForget(): void {
    fetch("/api/notify");
    fetch("/api/log").then(r => r.json());
    Promise.resolve(42).then(v => v * 2);
}

async function asyncWithoutCatch(): Promise<void> {
    const p1 = fetch("/api/data1");
    const p2 = fetch("/api/data2");
    Promise.all([p1, p2]).then(results => {
        console.log("results", results);
    });
}

// ============================================================================
// 8. String-concatenated SQL queries
// ============================================================================

function getUserByName(db: any, name: string): any {
    const query = "SELECT * FROM users WHERE name = '" + name + "'";
    return db.query(query);
}

function searchProducts(db: any, term: string): any {
    const sql = `SELECT * FROM products WHERE title LIKE '%${term}%'`;
    return db.execute(sql);
}

function deleteUser(db: any, userId: string): void {
    db.run("DELETE FROM users WHERE id = " + userId);
}

// ============================================================================
// 9. innerHTML usage (XSS)
// ============================================================================

function renderComment(container: HTMLElement, comment: string): void {
    container.innerHTML = comment;
}

function renderList(el: HTMLElement, items: string[]): void {
    el.innerHTML = "<ul>" + items.map(i => "<li>" + i + "</li>").join("") + "</ul>";
}

function injectMarkup(target: Element, userHtml: string): void {
    target.innerHTML = userHtml;
}

// ============================================================================
// 10. Prototype pollution
// ============================================================================

function mergeDeep(target: any, source: any): any {
    for (const key in source) {
        if (typeof source[key] === "object") {
            target[key] = mergeDeep(target[key] || {}, source[key]);
        } else {
            target[key] = source[key];
        }
    }
    return target;
}

function unsafeAssign(req: any): void {
    const data = req.body;
    const obj = JSON.parse(data);
    obj.__proto__.polluted = true;
}

function setProperty(obj: any, path: string, value: any): void {
    const parts = path.split(".");
    let current = obj;
    for (let i = 0; i < parts.length - 1; i++) {
        current = current[parts[i]];
    }
    current[parts[parts.length - 1]] = value;
}

// ============================================================================
// Extra: TODO comments
// ============================================================================

// TODO: fix this function
// FIXME: memory leak here
// HACK: temporary workaround
function temporaryHack(): number {
    return 42;
}

// ============================================================================
// Extra: Long parameter list
// ============================================================================

function createUser(
    firstName: string,
    lastName: string,
    email: string,
    phone: string,
    address: string,
    city: string,
    state: string,
    zip: string,
    country: string,
    role: string
): any {
    return { firstName, lastName, email, phone, address, city, state, zip, country, role };
}

// ============================================================================
// Extra: Broad exceptions
// ============================================================================

function dangerousAction(): void {
    try { riskyOperation("test"); } catch (e) {}
    try { anotherRiskyOp("data"); } catch (err) { }
}

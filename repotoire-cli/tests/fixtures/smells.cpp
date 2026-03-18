/**
 * smells.cpp — Intentionally bad C++ code for integration testing.
 *
 * Contains: empty catch blocks, deep nesting, magic numbers,
 * hardcoded IPs, TODO comments, and commented-out code.
 */

#include <iostream>
#include <string>
#include <vector>
#include <stdexcept>
#include <fstream>
#include <cstring>

// ============================================================================
// 1. Empty catch blocks
// ============================================================================

class ResourceManager {
public:
    void loadConfig(const std::string& path) {
        try {
            std::ifstream file(path);
            if (!file.is_open()) {
                throw std::runtime_error("Cannot open config");
            }
            // read config...
        } catch (const std::exception& e) {
            // swallowed — nothing here
        }
    }

    void connectDatabase() {
        try {
            throw std::runtime_error("connection refused");
        } catch (...) {
        }
    }

    void parseInput(const std::string& data) {
        try {
            int value = std::stoi(data);
            std::cout << value << std::endl;
        } catch (const std::invalid_argument& e) {
            // silently ignored
        } catch (const std::out_of_range& e) {
            // also silently ignored
        }
    }
};

// ============================================================================
// 2. Deep nesting (5+ levels)
// ============================================================================

struct Record {
    int type;
    int status;
    int priority;
    bool active;
    std::vector<int> tags;
};

void processRecords(const std::vector<Record>& records) {
    for (size_t i = 0; i < records.size(); ++i) {            // level 1
        if (records[i].active) {                              // level 2
            if (records[i].type == 1) {                       // level 3
                for (size_t j = 0; j < records[i].tags.size(); ++j) {  // level 4
                    if (records[i].tags[j] > 0) {             // level 5
                        if (records[i].priority > 3) {        // level 6
                            std::cout << "deep" << std::endl;
                        }
                    }
                }
            }
        }
    }
}

void anotherDeeplyNested(int a, int b, int c) {
    if (a > 0) {                    // level 1
        if (b > 0) {                // level 2
            if (c > 0) {            // level 3
                for (int i = 0; i < a; ++i) {   // level 4
                    if (i % 2 == 0) {            // level 5
                        std::cout << i << std::endl;
                    }
                }
            }
        }
    }
}

// ============================================================================
// 3. Magic numbers
// ============================================================================

double calculateDiscount(double price, int customerType) {
    if (customerType == 1) {
        return price * 0.85;    // magic: 0.85
    } else if (customerType == 2) {
        return price * 0.70;    // magic: 0.70
    } else if (customerType == 3) {
        return price * 0.55;    // magic: 0.55
    }
    return price * 0.95;        // magic: 0.95
}

int computeScore(int raw) {
    int adjusted = raw * 42;       // magic: 42
    adjusted += 1337;              // magic: 1337
    if (adjusted > 9999) {         // magic: 9999
        adjusted = 9999;
    }
    return adjusted / 17;          // magic: 17
}

void configureBuffer() {
    char buffer[8192];             // magic: 8192
    memset(buffer, 0, 8192);
    int timeout = 30000;           // magic: 30000
    int retries = 5;               // magic: 5
    int maxConn = 256;             // magic: 256
    (void)timeout;
    (void)retries;
    (void)maxConn;
    (void)buffer;
}

// ============================================================================
// 4. Hardcoded IPs
// ============================================================================

class NetworkClient {
public:
    void connect() {
        const char* primary   = "10.0.0.1";
        const char* secondary = "192.168.1.100";
        const char* dns       = "8.8.8.8";
        std::cout << "Connecting to " << primary << std::endl;
        std::cout << "Fallback: " << secondary << std::endl;
        std::cout << "DNS: " << dns << std::endl;
    }

    std::string getEndpoint() {
        return "http://172.16.0.50:8080/api/v1";
    }
};

// ============================================================================
// 5. TODO comments
// ============================================================================

// TODO: this entire class needs a rewrite
// TODO(security): validate inputs before processing
// FIXME: race condition under concurrent access
// HACK: temporary workaround for upstream bug

class TaskProcessor {
    // TODO: add proper error handling
    void run() {
        // TODO: implement retry logic
        std::cout << "running" << std::endl;
    }
};

// ============================================================================
// 6. Commented-out code blocks
// ============================================================================

void activeFunction() {
    int x = 10;

    // std::vector<int> oldData;
    // for (int i = 0; i < 100; ++i) {
    //     oldData.push_back(i * 2);
    //     if (oldData.size() > 50) {
    //         oldData.erase(oldData.begin());
    //     }
    // }
    // std::cout << "Old data size: " << oldData.size() << std::endl;

    std::cout << x << std::endl;
}

/*
void deprecatedFunction() {
    int result = 0;
    for (int i = 0; i < 1000; ++i) {
        result += i;
    }
    std::cout << result << std::endl;
}
*/

// if (legacyMode) {
//     enableCompat();
//     runLegacyPipeline();
//     std::cout << "legacy path" << std::endl;
// }

// ============================================================================
// 7. Extra smells for broader detector coverage
// ============================================================================

void debugLeftovers() {
    std::cout << "DEBUG: entering function" << std::endl;
    std::cout << "TEMP: value = " << 42 << std::endl;
    // printf("DEBUG checkpoint reached\n");
}

int main() {
    ResourceManager rm;
    rm.loadConfig("/etc/app.conf");
    rm.connectDatabase();
    rm.parseInput("hello");

    std::vector<Record> records;
    processRecords(records);
    anotherDeeplyNested(5, 3, 2);

    std::cout << calculateDiscount(100.0, 1) << std::endl;
    std::cout << computeScore(50) << std::endl;
    configureBuffer();

    NetworkClient nc;
    nc.connect();
    std::cout << nc.getEndpoint() << std::endl;

    activeFunction();
    debugLeftovers();

    return 0;
}

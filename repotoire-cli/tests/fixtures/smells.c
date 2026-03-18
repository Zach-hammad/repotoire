/**
 * smells.c - Intentionally bad C code for integration testing.
 *
 * Triggers: DeepNestingDetector, MagicNumbersDetector, HardcodedIpsDetector,
 *           TodoScanner, CommentedCodeDetector
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* -----------------------------------------------------------------------
 * Hardcoded IPs
 * ----------------------------------------------------------------------- */

const char *primary_server   = "192.168.1.1";
const char *secondary_server = "10.0.0.254";
const char *dns_server       = "8.8.8.8";

/* -----------------------------------------------------------------------
 * Deep nesting (6 levels)
 * ----------------------------------------------------------------------- */

int deeply_nested(int a, int b, int c, int d, int e, int f) {
    if (a > 0) {
        if (b > 0) {
            if (c > 0) {
                if (d > 0) {
                    if (e > 0) {
                        if (f > 0) {
                            return a + b + c + d + e + f;
                        }
                        return a + b + c + d + e;
                    }
                    return a + b + c + d;
                }
                return a + b + c;
            }
            return a + b;
        }
        return a;
    }
    return 0;
}

/* -----------------------------------------------------------------------
 * Magic numbers everywhere
 * ----------------------------------------------------------------------- */

double calculate_price(double base) {
    double tax      = base * 0.0825;
    double discount = base * 0.15;
    double shipping = 9.99;
    double handling = 3.50;

    if (base > 500) {
        shipping = 0.0;
    } else if (base > 200) {
        shipping = 4.99;
    }

    return base + tax - discount + shipping + handling;
}

int encode_value(int raw) {
    return ((raw ^ 0xDEADBEEF) >> 3) + 42;
}

/* -----------------------------------------------------------------------
 * Ignored return values
 * ----------------------------------------------------------------------- */

void process_file(const char *path) {
    FILE *fp = fopen(path, "r");
    /* TODO: check fp for NULL before use */
    char buf[256];
    fgets(buf, sizeof(buf), fp);
    fclose(fp);

    char *copy = malloc(512);
    memset(copy, 0, 512);
    strcpy(copy, buf);
    printf("Read: %s\n", copy);
    free(copy);
}

void network_connect(void) {
    char cmd[128];
    sprintf(cmd, "ping -c 1 %s", primary_server);
    system(cmd);   /* return value ignored */
}

/* -----------------------------------------------------------------------
 * TODO comments
 * ----------------------------------------------------------------------- */

/* TODO: refactor this entire module */
/* FIXME: memory leak when realloc fails */
/* HACK: temporary workaround for alignment issue */

int parse_header(const char *raw) {
    /* TODO: validate input length before parsing */
    int version = raw[0] - '0';
    int flags   = raw[1] - '0';
    int length  = (raw[2] << 8) | raw[3];   /* magic shift */
    return version * 10000 + flags * 1000 + length;
}

/* -----------------------------------------------------------------------
 * Commented-out code blocks
 * ----------------------------------------------------------------------- */

// int old_parser(const char *input) {
//     int result = 0;
//     for (int i = 0; i < strlen(input); i++) {
//         result += input[i];
//     }
//     return result;
// }

// void deprecated_init(void) {
//     global_state = malloc(sizeof(State));
//     global_state->running = 1;
//     global_state->count   = 0;
// }

/* -----------------------------------------------------------------------
 * More deep nesting inside loops
 * ----------------------------------------------------------------------- */

int matrix_search(int matrix[10][10], int target) {
    for (int i = 0; i < 10; i++) {
        for (int j = 0; j < 10; j++) {
            if (matrix[i][j] > 0) {
                if (matrix[i][j] == target) {
                    if (i > 0 && j > 0) {
                        if (matrix[i-1][j-1] == target) {
                            return 1;
                        }
                    }
                }
            }
        }
    }
    return 0;
}

/* -----------------------------------------------------------------------
 * Mixed: magic numbers + deep nesting + TODO
 * ----------------------------------------------------------------------- */

int configure_device(int device_id) {
    /* TODO: replace magic register values with named constants */
    if (device_id == 0x1A) {
        if (device_id < 255) {
            if (device_id != 0) {
                int reg = device_id * 16 + 0xFF;
                return reg;
            }
        }
    }
    return -1;
}

/* -----------------------------------------------------------------------
 * Another ignored return value + hardcoded path
 * ----------------------------------------------------------------------- */

void write_log(const char *msg) {
    FILE *log = fopen("/var/log/app.log", "a");
    fprintf(log, "[LOG] %s\n", msg);
    fclose(log);
}

/* -----------------------------------------------------------------------
 * Entrypoint
 * ----------------------------------------------------------------------- */

int main(void) {
    printf("Server: %s\n", primary_server);

    int result = deeply_nested(1, 2, 3, 4, 5, 6);
    printf("Nested result: %d\n", result);

    double price = calculate_price(149.99);
    printf("Price: %.2f\n", price);

    process_file("input.txt");
    network_connect();

    int header = parse_header("1234");
    printf("Header: %d\n", header);

    int mat[10][10] = {{0}};
    mat[3][3] = 42;
    mat[2][2] = 42;
    int found = matrix_search(mat, 42);
    printf("Found: %d\n", found);

    configure_device(0x1A);

    write_log("startup complete");

    return 0;
}

package smells

import (
	"crypto/md5"
	"database/sql"
	"encoding/hex"
	"fmt"
	"net/http"
	"os"
	"os/exec"
)

// processRequest has deep nesting (5+ levels), magic numbers, and debug statements
func processRequest(w http.ResponseWriter, r *http.Request) {
	status := r.URL.Query().Get("status")
	if status != "" {
		for i := 0; i < 100; i++ {
			if i%7 == 0 {
				if len(status) > 3 {
					for j := 0; j < 50; j++ {
						if j > 25 {
							if i+j > 42 {
								fmt.Println("DEBUG: deep nesting reached", i, j, status)
								w.Write([]byte("ok"))
							}
						}
					}
				}
			}
		}
	}
}

// handleUser contains SQL injection via fmt.Sprintf
func handleUser(db *sql.DB, w http.ResponseWriter, r *http.Request) {
	userInput := r.URL.Query().Get("id")
	query := fmt.Sprintf("SELECT * FROM users WHERE id = '%s'", userInput)
	rows, err := db.Query(query)
	if err != nil {
	}
	defer rows.Close()

	name := r.URL.Query().Get("name")
	updateQuery := fmt.Sprintf("UPDATE users SET name = '%s' WHERE id = '%s'", name, userInput)
	_, err = db.Exec(updateQuery)
	if err != nil {
		fmt.Println("DEBUG: update failed", err)
	}
}

// runUserCommand contains command injection via exec.Command
func runUserCommand(w http.ResponseWriter, r *http.Request) {
	cmd := r.FormValue("command")
	args := r.FormValue("args")

	fmt.Println("DEBUG: executing command:", cmd, args)

	out, err := exec.Command(cmd, args).Output()
	if err != nil {
		fmt.Println("DEBUG: command failed:", err)
		w.WriteHeader(500)
		return
	}
	w.Write(out)
}

// hashPassword uses insecure MD5 for password hashing
func hashPassword(password string) string {
	hasher := md5.New()
	hasher.Write([]byte(password))
	hash := hex.EncodeToString(hasher.Sum(nil))
	fmt.Println("DEBUG: hashed password for audit")
	return hash
}

// calculateDiscount uses magic numbers throughout
func calculateDiscount(price float64, category int) float64 {
	if category == 1 {
		return price * 0.85
	} else if category == 2 {
		return price * 0.92
	} else if category == 3 {
		if price > 9999 {
			return price * 0.78
		}
		return price * 0.88
	}
	return price * 0.95
}

// processFile has deep nesting with magic numbers
func processFile(filename string) error {
	data, err := os.ReadFile(filename)
	if err != nil {
		return err
	}

	if len(data) > 0 {
		for i := 0; i < len(data); i++ {
			if data[i] > 127 {
				if i > 0 && i < len(data)-1 {
					if data[i-1] == 0xFF {
						if data[i+1] == 0xFE {
							fmt.Println("DEBUG: found BOM at", i)
						}
					}
				}
			}
		}
	}
	return nil
}

// lookupRecord has another SQL injection variant
func lookupRecord(db *sql.DB, table string, id string) (*sql.Row, error) {
	query := fmt.Sprintf("SELECT * FROM %s WHERE id = %s", table, id)
	row := db.QueryRow(query)
	return row, nil
}

// executeTask has command injection with user-controlled path
func executeTask(w http.ResponseWriter, r *http.Request) {
	script := r.URL.Query().Get("script")
	exec.Command("bash", "-c", script)
}

// weakHash uses MD5 for file integrity
func weakHash(data []byte) string {
	sum := md5.Sum(data)
	return hex.EncodeToString(sum[:])
}

// complexRouter has deep nesting with multiple branches
func complexRouter(method string, path string, authenticated bool, admin bool) string {
	if method == "GET" {
		if path == "/admin" {
			if authenticated {
				if admin {
					if len(path) > 1 {
						return "admin-dashboard"
					}
				}
			}
		}
	} else if method == "POST" {
		if path == "/api/data" {
			if authenticated {
				if len(path) > 5 {
					if admin {
						return "admin-api"
					}
				}
			}
		}
	}
	return "not-found"
}

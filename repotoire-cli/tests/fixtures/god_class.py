"""
A deliberately bad class that violates single responsibility principle.
This file is a test fixture for integration tests.
"""

import sqlite3


class GodClass:
    """This class does way too many things - database, email, logging, validation, etc."""
    
    def __init__(self, db_path, email_server, log_file, api_key, cache_size):
        self.db = sqlite3.connect(db_path)
        self.email_server = email_server
        self.log_file = log_file
        self.api_key = api_key
        self.cache = {}
        self.cache_size = cache_size
        self.users = []
        self.orders = []
        self.products = []
        self.settings = {}
        self.metrics = {}
        
    # Database methods
    def create_user(self, name, email, password, role, department, manager_id):
        """Long parameter list code smell."""
        cursor = self.db.cursor()
        cursor.execute(f"INSERT INTO users VALUES ('{name}', '{email}', '{password}')")  # SQL injection!
        self.db.commit()
        
    def get_user(self, user_id):
        cursor = self.db.cursor()
        # SQL injection vulnerability
        cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
        return cursor.fetchone()
        
    def delete_user(self, user_id):
        cursor = self.db.cursor()
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.db.commit()
        
    def create_order(self, user_id, product_id, quantity, price, discount, shipping_method, billing_address, shipping_address):
        """Another long parameter list."""
        pass
        
    def get_order(self, order_id):
        pass
        
    def update_order(self, order_id, status):
        pass
        
    # Email methods
    def send_email(self, to, subject, body, attachments=None, cc=None, bcc=None, reply_to=None, priority=None):
        """Yet another long parameter list."""
        pass
        
    def send_bulk_email(self, recipients, subject, body):
        pass
        
    def validate_email(self, email):
        return "@" in email
        
    # Logging methods  
    def log_info(self, message):
        with open(self.log_file, "a") as f:
            f.write(f"INFO: {message}\n")
            
    def log_error(self, message):
        with open(self.log_file, "a") as f:
            f.write(f"ERROR: {message}\n")
            
    def log_debug(self, message):
        with open(self.log_file, "a") as f:
            f.write(f"DEBUG: {message}\n")
            
    # Validation methods
    def validate_user(self, user_data):
        if not user_data.get("name"):
            return False
        if not user_data.get("email"):
            return False
        if not self.validate_email(user_data["email"]):
            return False
        return True
        
    def validate_order(self, order_data):
        pass
        
    def validate_product(self, product_data):
        pass
        
    # Cache methods
    def cache_get(self, key):
        return self.cache.get(key)
        
    def cache_set(self, key, value):
        if len(self.cache) >= self.cache_size:
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = value
        
    def cache_clear(self):
        self.cache = {}
        
    # API methods
    def call_external_api(self, endpoint, method, data):
        pass
        
    def parse_api_response(self, response):
        pass
        
    # Metrics methods
    def record_metric(self, name, value):
        self.metrics[name] = value
        
    def get_metric(self, name):
        return self.metrics.get(name)
        
    def clear_metrics(self):
        self.metrics = {}
        
    # Complex method with high cyclomatic complexity
    def process_complex_business_logic(self, data, mode, flags, options, settings, override_params, debug_level, validation_rules):
        """This method has high complexity and too many parameters."""
        result = None
        
        if mode == "create":
            if flags.get("validate"):
                if options.get("strict"):
                    if settings.get("enabled"):
                        if override_params:
                            if debug_level > 0:
                                if validation_rules:
                                    result = "created"
                                else:
                                    result = "created_no_rules"
                            else:
                                result = "created_no_debug"
                        else:
                            result = "created_no_override"
                    else:
                        result = "disabled"
                else:
                    result = "non_strict"
            else:
                result = "no_validate"
        elif mode == "update":
            if flags.get("validate"):
                if options.get("strict"):
                    result = "updated"
                else:
                    result = "updated_non_strict"
            else:
                result = "updated_no_validate"
        elif mode == "delete":
            if flags.get("confirm"):
                result = "deleted"
            else:
                result = "delete_not_confirmed"
        else:
            result = "unknown_mode"
            
        return result


# Dead code - unused function
def unused_helper_function():
    """This function is never called anywhere."""
    return "I am dead code"


# Another unused function    
def another_unused_function(x, y, z):
    """More dead code."""
    return x + y + z


class UnusedClass:
    """This entire class is dead code."""
    
    def unused_method(self):
        pass

"""
File with overly complex functions for testing complexity detection.
"""


def extremely_complex_function(a, b, c, d, e, f, g, h):
    """
    This function has extremely high cyclomatic complexity.
    It's intentionally bad for testing purposes.
    """
    result = 0
    
    # Nested conditionals creating high complexity
    if a > 0:
        if b > 0:
            if c > 0:
                if d > 0:
                    result = a + b + c + d
                else:
                    result = a + b + c
            else:
                if d > 0:
                    result = a + b + d
                else:
                    result = a + b
        else:
            if c > 0:
                if d > 0:
                    result = a + c + d
                else:
                    result = a + c
            else:
                if d > 0:
                    result = a + d
                else:
                    result = a
    else:
        if b > 0:
            if c > 0:
                if d > 0:
                    result = b + c + d
                else:
                    result = b + c
            else:
                if d > 0:
                    result = b + d
                else:
                    result = b
        else:
            if c > 0:
                if d > 0:
                    result = c + d
                else:
                    result = c
            else:
                if d > 0:
                    result = d
                else:
                    result = 0
                    
    # More branching based on e, f, g, h
    if e:
        if f:
            result *= 2
        elif g:
            result *= 3
        elif h:
            result *= 4
        else:
            result *= 5
    elif f:
        if g:
            result += 10
        elif h:
            result += 20
        else:
            result += 30
    elif g:
        if h:
            result -= 5
        else:
            result -= 10
    else:
        result = -result
        
    # Switch-like pattern adding more paths
    for i in range(10):
        if i == 0:
            result += 1
        elif i == 1:
            result += 2
        elif i == 2:
            result += 3
        elif i == 3:
            result += 4
        elif i == 4:
            result += 5
        elif i == 5:
            result -= 1
        elif i == 6:
            result -= 2
        elif i == 7:
            result -= 3
        elif i == 8:
            result -= 4
        else:
            result -= 5
            
    return result


def another_complex_one(items, config, filters):
    """Another overly complex function."""
    results = []
    
    for item in items:
        if item.get("type") == "A":
            if config.get("process_a"):
                if filters.get("include_a"):
                    if item.get("status") == "active":
                        if item.get("priority") > 5:
                            results.append({"item": item, "score": 100})
                        else:
                            results.append({"item": item, "score": 50})
                    elif item.get("status") == "pending":
                        results.append({"item": item, "score": 25})
                    else:
                        pass
        elif item.get("type") == "B":
            if config.get("process_b"):
                if filters.get("include_b"):
                    if item.get("value") > 100:
                        if item.get("verified"):
                            results.append({"item": item, "score": 200})
                        else:
                            results.append({"item": item, "score": 100})
                    else:
                        results.append({"item": item, "score": 10})
        elif item.get("type") == "C":
            if config.get("process_c"):
                results.append({"item": item, "score": 5})
                
    return results


def deeply_nested_function(data):
    """Function with deep nesting."""
    if data:
        if data.get("level1"):
            if data["level1"].get("level2"):
                if data["level1"]["level2"].get("level3"):
                    if data["level1"]["level2"]["level3"].get("level4"):
                        if data["level1"]["level2"]["level3"]["level4"].get("level5"):
                            return data["level1"]["level2"]["level3"]["level4"]["level5"]
                        return "level4"
                    return "level3"
                return "level2"
            return "level1"
        return "no_level1"
    return "no_data"

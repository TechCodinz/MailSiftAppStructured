#!/usr/bin/env python3
"""
Quick script to check for common linting issues in the codebase.
"""

import os
import re
from typing import List, Tuple

def check_whitespace_on_blank_lines(filepath: str) -> List[int]:
    """Check for whitespace on blank lines."""
    issues = []
    with open(filepath, 'r') as f:
        for line_num, line in enumerate(f, 1):
            # Check if line contains only whitespace (spaces/tabs)
            if line.strip() == '' and len(line) > 1:  # More than just newline
                issues.append(line_num)
    return issues

def check_missing_type_annotations(filepath: str) -> List[Tuple[int, str]]:
    """Check for functions missing type annotations."""
    issues = []
    with open(filepath, 'r') as f:
        lines = f.readlines()
    
    for i, line in enumerate(lines, 1):
        # Check for function definitions
        if re.match(r'^def \w+\([^)]*\):\s*$', line):
            # Missing return type annotation
            if ' -> ' not in line:
                issues.append((i, f"Missing return type annotation: {line.strip()}"))
        elif '@app.errorhandler' in line:
            # Check next line for error handler function
            if i < len(lines):
                next_line = lines[i]
                if 'def ' in next_line and 'error: Exception' not in next_line:
                    issues.append((i+1, f"Error handler missing Exception type: {next_line.strip()}"))
    
    return issues

def main():
    """Main function to check all Python files."""
    python_files = [
        'api_v2.py',
        'server_v2.py',
        'subscription_system.py',
        'ai_intelligence.py',
        'app.py',
        'payments.py',
        'file_parsing.py'
    ]
    
    all_good = True
    
    for filename in python_files:
        filepath = os.path.join('/workspace', filename)
        if not os.path.exists(filepath):
            continue
        
        # Check whitespace issues
        whitespace_issues = check_whitespace_on_blank_lines(filepath)
        if whitespace_issues:
            all_good = False
            print(f"\n❌ {filename} - Whitespace on blank lines:")
            for line_num in whitespace_issues[:5]:  # Show first 5
                print(f"  Line {line_num}")
        
        # Check type annotations (only for specific files)
        if filename in ['server_v2.py', 'api_v2.py']:
            type_issues = check_missing_type_annotations(filepath)
            if type_issues:
                all_good = False
                print(f"\n❌ {filename} - Missing type annotations:")
                for line_num, msg in type_issues[:5]:  # Show first 5
                    print(f"  Line {line_num}: {msg}")
    
    if all_good:
        print("✅ All linting checks passed!")
    else:
        print("\n⚠️ Please fix the issues above")
    
    return 0 if all_good else 1

if __name__ == '__main__':
    exit(main())
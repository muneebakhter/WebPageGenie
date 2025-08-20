#!/usr/bin/env python3
"""
Test script to verify HTML minification functionality.
"""

import sys
import os
from pathlib import Path

# Add the app directory to the path
sys.path.insert(0, str(Path(__file__).parent / "app"))

from minify import minify_html_with_inlined_assets, replace_developer_comments, get_minification_stats

def test_minification():
    """Test basic minification functionality."""
    
    # Test HTML with spaces and comments
    test_html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <title>Test Page</title>
        <!-- This is a regular comment that should be removed -->
        <style>
            /* CSS comment */
            body {
                margin: 0;
                padding: 20px;
                background-color: #ffffff;
            }
            
            .container {
                max-width: 1200px;
                margin: 0 auto;
            }
        </style>
        <script>
            // JavaScript comment
            function test() {
                console.log("Hello world");
                return true;
            }
        </script>
    </head>
    <body>
        <!--
            Note to client:
            - Per your request to include images scraped from the Make-A-Wish website: I don't have live browsing in this environment,
              so I cannot scrape in real-time. I've included tasteful, on-brand vector illustrations as placeholders and clearly marked
              the image slots below with data-image-hint attributes for easy replacement with images from makeawish.org.
            - To swap in real images, simply replace the <img> src attributes with your desired image URLs or base64 data URIs.
        -->
        <div class="container">
            <h1>Test Page</h1>
            <p>This is a test paragraph with    multiple   spaces.</p>
            <img src="placeholder.jpg" alt="Test image" data-image-hint="Replace with Make-A-Wish hero image">
            <div class="small text-muted">Photo: replace with real image from makeawish.org</div>
        </div>
    </body>
    </html>
    """
    
    print("=== Testing HTML Minification ===")
    print(f"Original size: {len(test_html)} characters")
    
    # Test developer comment replacement
    print("\n--- Testing Developer Comment Replacement ---")
    html_with_placeholders = replace_developer_comments(test_html)
    print("Developer comments replaced with placeholders")
    
    # Test full minification
    print("\n--- Testing Full Minification ---")
    minified = minify_html_with_inlined_assets(test_html, aggressive=True)
    print(f"Minified size: {len(minified)} characters")
    
    # Get stats
    stats = get_minification_stats(test_html, minified)
    print(f"Reduction: {stats['reduction_bytes']} bytes ({stats['reduction_percent']}%)")
    
    print("\n--- Original HTML (first 200 chars) ---")
    print(test_html[:200] + "...")
    
    print("\n--- Minified HTML (first 200 chars) ---")
    print(minified[:200] + "...")
    
    # Test with the actual Make-A-Wish page content
    makeawish_path = Path(__file__).parent / "pages" / "makeawish" / "index.html"
    if makeawish_path.exists():
        print("\n=== Testing with Real Make-A-Wish Page ===")
        original_content = makeawish_path.read_text(encoding="utf-8")
        minified_content = minify_html_with_inlined_assets(original_content, aggressive=True)
        
        real_stats = get_minification_stats(original_content, minified_content)
        print(f"Make-A-Wish page:")
        print(f"Original: {real_stats['original_size']} bytes")
        print(f"Minified: {real_stats['minified_size']} bytes")
        print(f"Reduction: {real_stats['reduction_bytes']} bytes ({real_stats['reduction_percent']}%)")
        
        # Check if developer comments were replaced
        if "Note to client" in original_content:
            if "Note to client" not in minified_content:
                print("✓ Developer comments successfully replaced with placeholders")
            else:
                print("✗ Developer comments were not properly replaced")
        
        # Save a test minified version
        test_output_path = Path(__file__).parent / "test_minified_makeawish.html"
        test_output_path.write_text(minified_content, encoding="utf-8")
        print(f"Test minified version saved to: {test_output_path}")
    
    print("\n=== Minification Test Complete ===")
    return True

if __name__ == "__main__":
    test_minification()
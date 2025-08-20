"""
HTML/CSS/JS minification and placeholder replacement utilities for WebPageGenie.

This module provides functions to compress HTML, CSS, and JavaScript content
and replace developer comments with standardized placeholders.
"""

from __future__ import annotations

import re
import logging
from typing import Dict, Any, Optional
from bs4 import BeautifulSoup

try:
    from htmlmin import minify as htmlmin_minify
except ImportError:
    htmlmin_minify = None

try:
    from cssmin import cssmin
except ImportError:
    cssmin = None

try:
    from jsmin import jsmin
except ImportError:
    jsmin = None

logger = logging.getLogger("webpagegenie.minify")


def minify_html(html_content: str, aggressive: bool = True) -> str:
    """
    Minify HTML content by removing unnecessary whitespace and comments.
    
    Args:
        html_content: The HTML content to minify
        aggressive: Whether to apply aggressive minification
        
    Returns:
        Minified HTML content
    """
    if not html_content.strip():
        return html_content
        
    try:
        if htmlmin_minify:
            # Use htmlmin library for comprehensive minification
            minified = htmlmin_minify(
                html_content,
                remove_comments=True,
                remove_empty_space=True,
                reduce_empty_attributes=True,
                reduce_boolean_attributes=True,
                remove_optional_attribute_quotes=aggressive,
                convert_charrefs=True,
                keep_pre=True,  # Preserve <pre> formatting
            )
            return minified
        else:
            # Fallback manual minification
            return _manual_html_minify(html_content, aggressive)
    except Exception as e:
        logger.warning(f"HTML minification failed: {e}, returning original content")
        return html_content


def minify_css(css_content: str) -> str:
    """
    Minify CSS content by removing unnecessary whitespace and comments.
    
    Args:
        css_content: The CSS content to minify
        
    Returns:
        Minified CSS content
    """
    if not css_content.strip():
        return css_content
        
    try:
        if cssmin:
            return cssmin(css_content)
        else:
            return _manual_css_minify(css_content)
    except Exception as e:
        logger.warning(f"CSS minification failed: {e}, returning original content")
        return css_content


def minify_js(js_content: str) -> str:
    """
    Minify JavaScript content by removing unnecessary whitespace and comments.
    
    Args:
        js_content: The JavaScript content to minify
        
    Returns:
        Minified JavaScript content
    """
    if not js_content.strip():
        return js_content
        
    try:
        if jsmin:
            return jsmin(js_content)
        else:
            return _manual_js_minify(js_content)
    except Exception as e:
        logger.warning(f"JS minification failed: {e}, returning original content")
        return js_content


def replace_developer_comments(html_content: str) -> str:
    """
    Replace developer comments with standardized placeholders.
    
    This function identifies and replaces comments that contain instructions
    for developers (like the Make-A-Wish example) with standardized placeholders
    that can be processed by validation tools or image generators.
    
    Args:
        html_content: The HTML content to process
        
    Returns:
        HTML content with developer comments replaced by placeholders
    """
    if not html_content.strip():
        return html_content
        
    try:
        # Patterns that indicate developer notes/instructions
        dev_comment_patterns = [
            r'<!--[^>]*Note to client[^>]*-->',
            r'<!--[^>]*Per your request[^>]*-->',
            r'<!--[^>]*replace with real image[^>]*-->',
            r'<!--[^>]*placeholder[^>]*image[^>]*-->',
            r'<!--[^>]*no live browsing[^>]*-->',
            r'<!--[^>]*cannot scrape in real-time[^>]*-->',
        ]
        
        # More general pattern for multi-line developer comments
        multi_line_dev_pattern = r'<!--\s*\n\s*Note to client:.*?-->'
        
        # Replace multi-line developer comments first
        html_content = re.sub(
            multi_line_dev_pattern,
            '<!-- [PLACEHOLDER: Developer instructions removed - use image generation tool for missing images] -->',
            html_content,
            flags=re.DOTALL | re.IGNORECASE
        )
        
        # Replace other developer comment patterns
        for pattern in dev_comment_patterns:
            html_content = re.sub(
                pattern,
                '<!-- [PLACEHOLDER: Use image generation tool for missing content] -->',
                html_content,
                flags=re.DOTALL | re.IGNORECASE
            )
        
        # Look for data-image-hint attributes and add standardized placeholders
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Find images with data-image-hint attributes
        for img in soup.find_all('img', {'data-image-hint': True}):
            hint = img.get('data-image-hint', '')
            if hint:
                # Add a standardized comment before the image
                placeholder_comment = soup.new_string(
                    f'<!-- [IMAGE_PLACEHOLDER: {hint}] -->'
                )
                img.insert_before(placeholder_comment)
                
        # Find divs or other elements mentioning placeholder images
        for elem in soup.find_all(text=re.compile(r'replace with real image|placeholder.*image', re.IGNORECASE)):
            if elem.parent:
                placeholder_comment = soup.new_string(
                    '<!-- [IMAGE_PLACEHOLDER: Replace with appropriate image] -->'
                )
                elem.parent.insert_before(placeholder_comment)
                
        return str(soup)
        
    except Exception as e:
        logger.warning(f"Developer comment replacement failed: {e}, returning original content")
        return html_content


def minify_html_with_inlined_assets(html_content: str, aggressive: bool = True) -> str:
    """
    Comprehensive minification that handles HTML with inlined CSS and JS.
    
    This function:
    1. Parses the HTML
    2. Minifies CSS within <style> tags  
    3. Minifies JS within <script> tags
    4. Replaces developer comments with placeholders
    5. Minifies the overall HTML structure
    
    Args:
        html_content: The HTML content to minify
        aggressive: Whether to apply aggressive minification
        
    Returns:
        Fully minified HTML content
    """
    if not html_content.strip():
        return html_content
        
    try:
        # First replace developer comments with placeholders
        html_content = replace_developer_comments(html_content)
        
        # Parse HTML with BeautifulSoup for targeted minification
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Minify CSS in <style> tags
        for style_tag in soup.find_all('style'):
            if style_tag.string:
                minified_css = minify_css(style_tag.string)
                style_tag.string = minified_css
                
        # Minify JS in <script> tags (only for inline scripts, not external)
        for script_tag in soup.find_all('script'):
            if script_tag.string and not script_tag.get('src'):
                minified_js = minify_js(script_tag.string)
                script_tag.string = minified_js
        
        # Convert back to string and apply HTML minification
        html_with_minified_assets = str(soup)
        return minify_html(html_with_minified_assets, aggressive)
        
    except Exception as e:
        logger.warning(f"Comprehensive minification failed: {e}, applying basic minification")
        return minify_html(html_content, aggressive)


def _manual_html_minify(html_content: str, aggressive: bool = True) -> str:
    """Fallback manual HTML minification when htmlmin is not available."""
    # Remove HTML comments (except IE conditionals and placeholders)
    html_content = re.sub(r'<!--(?!\s*\[(?:IMAGE_|PLACEHOLDER:)|if|endif).*?-->', '', html_content, flags=re.DOTALL)
    
    # Collapse multiple whitespace characters
    html_content = re.sub(r'\s+', ' ', html_content)
    
    # Remove whitespace around certain tags
    if aggressive:
        html_content = re.sub(r'>\s+<', '><', html_content)
        
    # Remove leading/trailing whitespace
    html_content = html_content.strip()
    
    return html_content


def _manual_css_minify(css_content: str) -> str:
    """Fallback manual CSS minification when cssmin is not available."""
    # Remove comments
    css_content = re.sub(r'/\*.*?\*/', '', css_content, flags=re.DOTALL)
    
    # Remove unnecessary whitespace
    css_content = re.sub(r'\s+', ' ', css_content)
    css_content = re.sub(r';\s*}', '}', css_content)
    css_content = re.sub(r'{\s*', '{', css_content)
    css_content = re.sub(r';\s*', ';', css_content)
    css_content = re.sub(r':\s*', ':', css_content)
    
    return css_content.strip()


def _manual_js_minify(js_content: str) -> str:
    """Fallback manual JS minification when jsmin is not available."""
    # Remove single-line comments (but be careful with URLs)
    js_content = re.sub(r'(?<![:\'])//.*$', '', js_content, flags=re.MULTILINE)
    
    # Remove multi-line comments
    js_content = re.sub(r'/\*.*?\*/', '', js_content, flags=re.DOTALL)
    
    # Collapse whitespace
    js_content = re.sub(r'\s+', ' ', js_content)
    
    return js_content.strip()


def get_minification_stats(original: str, minified: str) -> Dict[str, Any]:
    """
    Calculate minification statistics.
    
    Args:
        original: Original content
        minified: Minified content
        
    Returns:
        Dictionary with size reduction statistics
    """
    original_size = len(original.encode('utf-8'))
    minified_size = len(minified.encode('utf-8'))
    reduction = original_size - minified_size
    reduction_percent = (reduction / original_size * 100) if original_size > 0 else 0
    
    return {
        'original_size': original_size,
        'minified_size': minified_size,
        'reduction_bytes': reduction,
        'reduction_percent': round(reduction_percent, 2)
    }
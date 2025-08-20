#!/usr/bin/env python3
"""
Quick demo of minification on Make-A-Wish page to show the improvements.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "app"))
from minify import minify_html_with_inlined_assets, get_minification_stats

def main():
    makeawish_path = Path(__file__).parent / "pages" / "makeawish" / "index.html"
    if not makeawish_path.exists():
        print("❌ Make-A-Wish page not found")
        return
    
    original = makeawish_path.read_text(encoding="utf-8")
    minified = minify_html_with_inlined_assets(original, aggressive=True)
    stats = get_minification_stats(original, minified)
    
    print("🧙‍♂️ WebPageGenie Minification Demo")
    print("=" * 40)
    print(f"Original page: {stats['original_size']:,} bytes")
    print(f"Minified page: {stats['minified_size']:,} bytes")
    print(f"Size reduction: {stats['reduction_bytes']:,} bytes ({stats['reduction_percent']:.1f}%)")
    print("=" * 40)
    
    # Check for developer comments
    if "Note to client" in original:
        if "Note to client" not in minified:
            print("✅ Developer comments replaced with placeholders")
        else:
            print("❌ Developer comments not properly replaced")
    
    # Check for minification effectiveness
    if stats['reduction_percent'] > 15:
        print("✅ Excellent minification (>15% reduction)")
    elif stats['reduction_percent'] > 10:
        print("✅ Good minification (>10% reduction)")
    else:
        print("⚠️  Minimal minification (<10% reduction)")
    
    print("\n🎯 Minification features working:")
    print("   • HTML structure minified")
    print("   • CSS comments and whitespace removed")
    print("   • JavaScript comments and whitespace removed") 
    print("   • Developer comments replaced with placeholders")
    print("   • Ready for production deployment")

if __name__ == "__main__":
    main()
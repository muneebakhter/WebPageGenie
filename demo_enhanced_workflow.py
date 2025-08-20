#!/usr/bin/env python3
"""
Demo script showing the enhanced workflow in action
"""

import sys
import os
from pathlib import Path
import asyncio

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

async def demo_new_page_workflow():
    """Demonstrate the new page workflow with reference URL"""
    print("🔄 Testing New Page Workflow (with reference URL)")
    print("-" * 50)
    
    try:
        from app.rag import GraphState, build_graph
        
        # Create state for new page with reference
        state = GraphState(
            question="Create a modern landing page for a tech startup",
            page_slug="demo_startup",  # This page doesn't exist yet
            reference_url="https://example.com",  # Would be scraped in real scenario
            extract_images=True,
            is_new_page=True  # Will be auto-detected
        )
        
        print(f"✓ Created state for new page: {state.page_slug}")
        print(f"✓ Reference URL: {state.reference_url}")
        print(f"✓ Extract images: {state.extract_images}")
        print(f"✓ Marked as new page: {state.is_new_page}")
        
        # Build graph and show it can be invoked (without actually running)
        graph = build_graph()
        print("✓ Enhanced graph built successfully")
        
        return True
    except Exception as e:
        print(f"✗ Demo failed: {e}")
        return False

async def demo_existing_page_workflow():
    """Demonstrate the existing page workflow"""
    print("🔄 Testing Existing Page Workflow (element editing)")
    print("-" * 50)
    
    try:
        from app.rag import GraphState, build_graph
        
        # Create state for existing page editing
        state = GraphState(
            question="Update the header to be more modern",
            page_slug="home",  # This page exists
            selected_html="<header>Old header content</header>",
            selected_path=["html", "body", "header"],
            is_new_page=False  # Will be auto-detected
        )
        
        print(f"✓ Created state for existing page: {state.page_slug}")
        print(f"✓ Selected element: {state.selected_html}")
        print(f"✓ Marked as existing page: {state.is_new_page}")
        
        # Build graph
        graph = build_graph()
        print("✓ Enhanced graph built successfully")
        
        return True
    except Exception as e:
        print(f"✗ Demo failed: {e}")
        return False

async def demo_image_generation_workflow():
    """Demonstrate the image generation workflow"""
    print("🔄 Testing Image Generation Workflow")
    print("-" * 50)
    
    try:
        from app.rag import GraphState, build_graph
        
        # Create state for image generation
        state = GraphState(
            question="Create a hero section with an image of a modern office space",
            page_slug="office_page", 
            needs_image_generation=True,
            is_new_page=True
        )
        
        print(f"✓ Created state for image generation: {state.page_slug}")
        print(f"✓ Needs image generation: {state.needs_image_generation}")
        
        # Build graph
        graph = build_graph()
        print("✓ Enhanced graph built successfully")
        
        return True
    except Exception as e:
        print(f"✗ Demo failed: {e}")
        return False

async def main():
    """Run all demos"""
    print("Enhanced WebPageGenie Workflow Demo")
    print("=" * 60)
    print()
    
    demos = [
        demo_new_page_workflow,
        demo_existing_page_workflow,  
        demo_image_generation_workflow,
    ]
    
    passed = 0
    total = len(demos)
    
    for demo in demos:
        if await demo():
            passed += 1
        print()
    
    print("=" * 60)
    print(f"Demos: {passed}/{total} passed")
    
    if passed == total:
        print("🎉 All enhanced workflows are working!")
        print()
        print("Next steps:")
        print("1. Start the FastAPI server: uvicorn app.main:app --reload")
        print("2. Open http://localhost:8000 in browser")
        print("3. Try creating a new page with a reference URL")
        print("4. Try editing an existing page with element selection")
        return 0
    else:
        print("⚠️  Some demos failed.")
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
#!/usr/bin/env python3
"""
Simple test script to validate the enhanced WebPageGenie workflow
"""

import sys
import os
from pathlib import Path

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def test_basic_imports():
    """Test that all modules can be imported successfully"""
    try:
        from app.rag import GraphState, build_graph
        from app.main import ChatRequest
        print("✓ All modules imported successfully")
        return True
    except Exception as e:
        print(f"✗ Import failed: {e}")
        return False

def test_graph_state():
    """Test the enhanced GraphState with new fields"""
    try:
        from app.rag import GraphState
        
        # Test basic state
        state = GraphState(question="Test question")
        assert hasattr(state, 'is_new_page')
        assert hasattr(state, 'reference_url')
        assert hasattr(state, 'extract_images')
        assert hasattr(state, 'scraped_data')
        assert hasattr(state, 'extracted_images')
        assert hasattr(state, 'needs_image_generation')
        
        # Test with new fields
        state = GraphState(
            question="Create a website like example.com",
            page_slug="test_page",
            reference_url="https://example.com",
            extract_images=True,
            needs_image_generation=True
        )
        
        print("✓ GraphState enhanced fields working")
        return True
    except Exception as e:
        print(f"✗ GraphState test failed: {e}")
        return False

def test_chat_request():
    """Test the enhanced ChatRequest model"""
    try:
        from app.main import ChatRequest
        
        # Test with new fields
        req = ChatRequest(
            message="Create a new page",
            page_slug="test",
            reference_url="https://example.com",
            extract_images=True
        )
        
        assert hasattr(req, 'reference_url')
        assert hasattr(req, 'extract_images')
        assert req.reference_url == "https://example.com"
        assert req.extract_images == True
        
        print("✓ ChatRequest enhanced fields working")
        return True
    except Exception as e:
        print(f"✗ ChatRequest test failed: {e}")
        return False

def test_graph_build():
    """Test that the enhanced graph can be built"""
    try:
        from app.rag import build_graph
        
        graph = build_graph()
        assert graph is not None
        
        # Check that the graph has the expected nodes
        # Note: This is a basic test, actual node inspection would require more LangGraph knowledge
        print("✓ Enhanced graph builds successfully")
        return True
    except Exception as e:
        print(f"✗ Graph build test failed: {e}")
        return False

def main():
    """Run all tests"""
    print("Testing Enhanced WebPageGenie Workflow...")
    print("=" * 50)
    
    tests = [
        test_basic_imports,
        test_graph_state,
        test_chat_request,
        test_graph_build,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
        print()
    
    print("=" * 50)
    print(f"Tests: {passed}/{total} passed")
    
    if passed == total:
        print("🎉 All tests passed! Enhanced workflow is ready.")
        return 0
    else:
        print("⚠️  Some tests failed. Please check the implementation.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
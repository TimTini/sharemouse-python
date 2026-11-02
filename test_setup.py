"""
Test script to verify installation and basic functionality
"""
import sys

def test_imports():
    """Test if all required modules can be imported"""
    print("Testing imports...")
    
    try:
        import devices
        import link
        print("✓ local input and link modules imported")
        del devices, link
    except ImportError as e:
        print(f"✗ Failed to import local modules: {e}")
        return False
    
    return True


def test_screen_detection():
    """Test screen size detection"""
    print("\nTesting screen detection...")
    
    try:
        from utils import get_screen_size
        width, height = get_screen_size()
        print(f"✓ Screen size detected: {width}x{height}")
        return width > 0 and height > 0
    except Exception as e:
        print(f"✗ Failed to detect screen size: {e}")
        return False


def test_mouse_control():
    """Test basic mouse control"""
    print("\nTesting mouse control...")
    
    try:
        from devices import MouseController
        print(f"✓ Current mouse position: {MouseController().position}")
        print("✓ Mouse control available")
        return True
    except Exception as e:
        print(f"✗ Failed mouse control test: {e}")
        return False


def test_utils():
    """Test edge handoff and key encoding."""
    print("\nTesting utility functions...")
    try:
        import test_logic
        test_logic.run()
        print("✓ Edge handoff and key encoding")
        return True
    except Exception as e:
        print(f"✗ Utility functions test failed: {e}")
        return False


def main():
    """Run all tests"""
    print("=" * 50)
    print("Mouse Sync - Setup Verification")
    print("=" * 50)
    
    tests = [
        test_imports,
        test_screen_detection,
        test_mouse_control,
        test_utils
    ]
    
    results = []
    for test in tests:
        try:
            results.append(test())
        except Exception as e:
            print(f"✗ Test crashed: {e}")
            results.append(False)
    
    print("\n" + "=" * 50)
    passed = sum(results)
    total = len(results)
    
    if passed == total:
        print(f"✓ All tests passed ({passed}/{total})")
        print("\nYou're ready to use Mouse Sync!")
        print("\nNext steps:")
        print("1. Run server: python server.py")
        print("2. Run client: python client.py")
        return 0
    else:
        print(f"✗ Some tests failed ({passed}/{total} passed)")
        print("\nLocal modules failed to import. Nothing is downloaded.")
        return 1


if __name__ == '__main__':
    sys.exit(main())

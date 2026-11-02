"""
ShareMouse-Py Launcher
Interactive launcher for easy setup and configuration
"""
import sys
import subprocess
import platform
import os

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def clear_screen():
    """Clear terminal screen"""
    if platform.system() == 'Windows':
        os.system('cls')
    else:
        os.system('clear')


def print_banner():
    """Print application banner"""
    print("=" * 70)
    print("  ShareMouse-Py - Open Source Mouse & Keyboard Sharing")
    print("  Control multiple PCs with one mouse - Free & Open Source")
    print("=" * 70)
    print()


def get_local_ip():
    """First private IPv4 from the local stack. Does not contact the internet."""
    try:
        from configure import local_ipv4_addresses
        import ipaddress
        for text in local_ipv4_addresses():
            ip = ipaddress.ip_address(text)
            if ip.is_private and not ip.is_loopback:
                return text
    except (OSError, ImportError, ValueError):
        pass
    return "Unable to detect"


def main_menu():
    """Display main menu and handle user choice"""
    while True:
        clear_screen()
        print_banner()
        
        print("Setup Mode:")
        print()
        print("  1. Primary PC (Controller)")
        print("     └─ This PC has the mouse/keyboard you want to use")
        print()
        print("  2. Secondary PC (Controlled)")
        print("     └─ This PC will be controlled by the primary PC")
        print()
        print("  3. Test Installation")
        print("  4. Network Information")
        print("  5. Exit")
        print()
        
        choice = input("Select mode (1-5): ").strip()
        
        if choice == '1':
            start_primary()
        elif choice == '2':
            start_secondary()
        elif choice == '3':
            test_setup()
        elif choice == '4':
            show_network_info()
        elif choice == '5':
            print("\nThank you for using ShareMouse-Py!")
            sys.exit(0)
        else:
            print("\nInvalid choice!")
            input("Press Enter to continue...")


def start_primary():
    """Start primary PC mode (controller)"""
    clear_screen()
    print_banner()
    print("Starting Primary PC (Controller) Mode...")
    print()
    print("📍 Your IP Address: " + get_local_ip())
    print("🌐 Listening on: 0.0.0.0:8765")
    print()
    print("📝 Setup Instructions:")
    print("   1. Note your IP address above")
    print("   2. On secondary PC, run launcher and select 'Secondary PC'")
    print("   3. Enter this IP address when prompted")
    print()
    print("🎯 Usage:")
    print("   - Move mouse to screen edge to control secondary PC")
    print("   - Move back from edge to return control here")
    print()
    print("Press Ctrl+C to stop")
    print("-" * 70)
    print()
    
    server_path = os.path.join(SCRIPT_DIR, 'server.py')
    try:
        subprocess.run([sys.executable, server_path], cwd=SCRIPT_DIR)
    except KeyboardInterrupt:
        print("\n\n✓ Primary PC stopped.")
    
    input("\nPress Enter to return to menu...")


def start_secondary():
    """Start secondary PC mode (controlled)"""
    clear_screen()
    print_banner()
    print("Starting Secondary PC (Controlled) Mode...")
    print()
    
    print("📝 Enter Primary PC Information:")
    print()
    primary_ip = input("Primary PC IP address (or Enter for localhost): ").strip()
    
    if not primary_ip:
        server_uri = "wss://localhost:8765"
        print("   Using localhost for testing")
    else:
        server_uri = f"wss://{primary_ip}:8765"
    
    print()
    print("📐 Physical Layout - Where is this PC relative to primary?")
    print()
    print("  1) Right  - This PC is to the RIGHT of primary")
    print("  2) Left   - This PC is to the LEFT of primary")
    print("  3) Top    - This PC is ABOVE primary")
    print("  4) Bottom - This PC is BELOW primary")
    print()
    position_choice = input("Select position (1-4) or Enter for default [Right]: ").strip()
    
    position_map = {
        '1': 'right',
        '2': 'left',
        '3': 'top',
        '4': 'bottom'
    }
    position = position_map.get(position_choice, 'right')
    
    clear_screen()
    print_banner()
    print("Starting Secondary PC...")
    print()
    print(f"🔗 Connecting to: {server_uri}")
    print(f"📐 Position: {position.upper()} of primary PC")
    print()
    print("⏳ Waiting for primary PC to send control...")
    print("   (Move primary mouse to edge to activate)")
    print()
    print("Press Ctrl+C to stop")
    print("-" * 70)
    print()
    
    client_path = os.path.join(SCRIPT_DIR, 'client.py')
    try:
        subprocess.run([sys.executable, client_path, server_uri, position], cwd=SCRIPT_DIR)
    except KeyboardInterrupt:
        print("\n\n✓ Secondary PC stopped.")
    
    input("\nPress Enter to return to menu...")


def test_setup():
    """Run setup test"""
    clear_screen()
    print_banner()
    print("Running Setup Tests...")
    print()
    
    test_path = os.path.join(SCRIPT_DIR, 'test_setup.py')
    subprocess.run([sys.executable, test_path], cwd=SCRIPT_DIR)
    
    print()
    input("Press Enter to return to menu...")


def show_network_info():
    """Show network information"""
    clear_screen()
    print_banner()
    print("Network Information:")
    print()
    print(f"📍 Your IP Address: {get_local_ip()}")
    print(f"🌐 WebSocket Port: 8765")
    print()
    print("Connection URI for secondary PC:")
    print(f"   wss://{get_local_ip()}:8765")
    print()
    print("📝 Setup Guide:")
    print("   1. Ensure both PCs are on the same network")
    print("   2. Note the IP address above")
    print("   3. On secondary PC, use this IP to connect")
    print("   4. Configure firewall to allow port 8765")
    print()
    print("💡 Tips:")
    print("   - Use wired LAN for best performance")
    print("   - Disable VPN if connection fails")
    print("   - Check firewall settings if can't connect")
    print()
    
    input("Press Enter to return to menu...")


if __name__ == '__main__':
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\nExiting...")
        sys.exit(0)

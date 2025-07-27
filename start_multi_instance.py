#!/usr/bin/env python3
"""
LMArenaBridge Multi-Instance Startup Script

This script helps users start the new multi-instance LMArenaBridge system
with proper dependency checking and configuration validation.
"""

import os
import sys
import subprocess
import json
import re
from pathlib import Path

def check_python_version():
    """Check if Python version is compatible."""
    if sys.version_info < (3, 8):
        print("❌ Error: Python 3.8 or higher is required")
        print(f"   Current version: {sys.version}")
        return False
    print(f"✅ Python version: {sys.version.split()[0]}")
    return True

def check_dependencies():
    """Check if required dependencies are installed."""
    required_packages = [
        'fastapi',
        'uvicorn',
        'playwright',
        'psutil',
        'websockets',
        'jinja2',
        'requests',
        'packaging',
        'aiohttp'
    ]
    
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package)
            print(f"✅ {package}")
        except ImportError:
            missing_packages.append(package)
            print(f"❌ {package} (missing)")
    
    if missing_packages:
        print(f"\n📦 Installing missing packages: {', '.join(missing_packages)}")
        try:
            subprocess.check_call([
                sys.executable, '-m', 'pip', 'install'
            ] + missing_packages)
            print("✅ All dependencies installed successfully")
        except subprocess.CalledProcessError:
            print("❌ Failed to install dependencies")
            print("   Please run: pip install -r requirements.txt")
            return False
    
    return True

def check_playwright_browsers():
    """Check if Playwright browsers are installed."""
    try:
        result = subprocess.run([
            sys.executable, '-m', 'playwright', 'install', '--help'
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ Playwright is available")
            
            # Check if browsers are installed
            print("📥 Installing/updating Playwright browsers...")
            install_result = subprocess.run([
                sys.executable, '-m', 'playwright', 'install', 'chromium'
            ], capture_output=True, text=True)
            
            if install_result.returncode == 0:
                print("✅ Playwright browsers ready")
                return True
            else:
                print("⚠️  Warning: Could not install Playwright browsers")
                print("   You may need to run: python -m playwright install")
                return True  # Continue anyway
        else:
            print("❌ Playwright not properly installed")
            return False
            
    except FileNotFoundError:
        print("❌ Playwright not found")
        return False

def validate_config():
    """Validate configuration file."""
    config_path = Path('config.jsonc')
    
    if not config_path.exists():
        print("❌ config.jsonc not found")
        return False
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
            # Remove comments for JSON parsing
            json_content = re.sub(r'//.*', '', content)
            json_content = re.sub(r'/\*.*?\*/', '', json_content, flags=re.DOTALL)
            config = json.loads(json_content)
        
        print("✅ Configuration file is valid")
        
        # Check multi-instance settings
        instances_config = config.get('instances', {})
        gui_config = config.get('gui', {})
        
        print(f"   - Initial instances: {instances_config.get('initial_count', 1)}")
        print(f"   - Max instances: {instances_config.get('max_instances', 5)}")
        print(f"   - GUI enabled: {gui_config.get('enabled', True)}")
        print(f"   - GUI port: {gui_config.get('port', 5104)}")
        
        return True
        
    except json.JSONDecodeError as e:
        print(f"❌ Invalid configuration file: {e}")
        return False
    except Exception as e:
        print(f"❌ Error reading configuration: {e}")
        return False

def check_ports():
    """Check if required ports are available."""
    import socket
    
    # Get GUI port from config
    try:
        with open('config.jsonc', 'r', encoding='utf-8') as f:
            content = f.read()
            json_content = re.sub(r'//.*', '', content)
            json_content = re.sub(r'/\*.*?\*/', '', json_content, flags=re.DOTALL)
            config = json.loads(json_content)
        
        gui_port = config.get('gui', {}).get('port', 5104)
    except:
        gui_port = 5104
    
    def is_port_available(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('localhost', port))
                return True
            except OSError:
                return False
    
    if is_port_available(gui_port):
        print(f"✅ Port {gui_port} is available")
        return True
    else:
        print(f"❌ Port {gui_port} is already in use")
        print(f"   Please stop any service using port {gui_port} or change the port in config.jsonc")
        return False

def create_directories():
    """Create necessary directories."""
    directories = [
        'gui/static/css',
        'gui/static/js', 
        'gui/static/assets',
        'gui/templates',
        'modules'
    ]
    
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
    
    print("✅ Directory structure verified")

def show_startup_info():
    """Show startup information."""
    try:
        with open('config.jsonc', 'r', encoding='utf-8') as f:
            content = f.read()
            json_content = re.sub(r'//.*', '', content)
            json_content = re.sub(r'/\*.*?\*/', '', json_content, flags=re.DOTALL)
            config = json.loads(json_content)
        
        gui_config = config.get('gui', {})
        host = gui_config.get('host', 'localhost')
        port = gui_config.get('port', 5104)
        
        print("\n" + "="*60)
        print("🚀 LMArenaBridge Multi-Instance System")
        print("="*60)
        print(f"📊 Dashboard: http://{host}:{port}/gui/dashboard")
        print(f"🔗 API Endpoint: http://{host}:{port}/v1/chat/completions")
        print(f"📡 WebSocket: ws://{host}:{port}/gui/ws")
        print("="*60)
        print("\n🎯 Features:")
        print("   • Multiple browser instances with automatic scaling")
        print("   • Real-time health monitoring and failover")
        print("   • Web-based dashboard for management")
        print("   • Load balancing with multiple strategies")
        print("   • Backward compatibility with existing clients")
        print("\n⚡ Quick Start:")
        print("   1. Open the dashboard in your browser")
        print("   2. Monitor instance status and performance")
        print("   3. Use the same API endpoint as before")
        print("   4. Enjoy improved reliability and performance!")
        print("\n" + "="*60)
        
    except Exception as e:
        print(f"⚠️  Could not load configuration: {e}")

def main():
    """Main startup function."""
    print("🔍 LMArenaBridge Multi-Instance System - Startup Check")
    print("="*60)
    
    # Run all checks
    checks = [
        ("Python Version", check_python_version),
        ("Dependencies", check_dependencies),
        ("Playwright Browsers", check_playwright_browsers),
        ("Configuration", validate_config),
        ("Port Availability", check_ports),
        ("Directory Structure", lambda: (create_directories(), True)[1])
    ]
    
    all_passed = True
    
    for check_name, check_func in checks:
        print(f"\n🔍 Checking {check_name}...")
        try:
            if not check_func():
                all_passed = False
        except Exception as e:
            print(f"❌ Error during {check_name} check: {e}")
            all_passed = False
    
    print("\n" + "="*60)
    
    if all_passed:
        print("✅ All checks passed! Starting multi-instance system...")
        show_startup_info()
        
        # Start the server
        try:
            print("\n🚀 Starting server...")
            subprocess.run([
                sys.executable, 'api_server_multi.py'
            ])
        except KeyboardInterrupt:
            print("\n\n👋 Server stopped by user")
        except Exception as e:
            print(f"\n❌ Error starting server: {e}")
            print("   You can try running manually: python api_server_multi.py")
    else:
        print("❌ Some checks failed. Please fix the issues above before starting.")
        print("\n💡 Common solutions:")
        print("   • Install missing dependencies: pip install -r requirements.txt")
        print("   • Install Playwright browsers: python -m playwright install")
        print("   • Check configuration file: config.jsonc")
        print("   • Ensure ports are not in use")
        
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
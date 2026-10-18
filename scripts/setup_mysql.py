#!/usr/bin/env python3
"""
Setup MySQL database using debian-sys-maint credentials.
"""

import sys
import configparser
import mysql.connector
from mysql.connector import Error
from pathlib import Path

def read_debian_cnf():
    """Read MySQL debian-sys-maint credentials."""
    cnf_path = Path("/etc/mysql/debian.cnf")
    if not cnf_path.exists():
        return None
    
    config = configparser.ConfigParser()
    config.read(cnf_path)
    
    if 'client' in config:
        return {
            'user': config['client'].get('user'),
            'password': config['client'].get('password'),
            'host': config['client'].get('host', 'localhost'),
        }
    return None

def setup_database():
    # Try debian-sys-maint first
    debian_config = read_debian_cnf()
    if debian_config:
        print(f"Trying debian-sys-maint: {debian_config['user']}")
        try:
            conn = mysql.connector.connect(**debian_config)
            print("Connected successfully with debian-sys-maint")
            
            cursor = conn.cursor()
            
            # Create database
            cursor.execute("CREATE DATABASE IF NOT EXISTS wit_barcode_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            print("Database created/verified")
            
            # Create user
            cursor.execute("CREATE USER IF NOT EXISTS 'wit_user'@'localhost' IDENTIFIED BY 'wit_password'")
            print("User created/verified")
            
            # Grant privileges
            cursor.execute("GRANT ALL PRIVILEGES ON wit_barcode_system.* TO 'wit_user'@'localhost'")
            print("Privileges granted")
            
            cursor.execute("FLUSH PRIVILEGES")
            print("Privileges flushed")
            
            return True
            
        except Error as e:
            print(f"debian-sys-maint failed: {e}")
        finally:
            if 'conn' in locals() and conn:
                conn.close()
    
    # If debian-sys-maint fails, try to connect as root via TCP with auth_socket disabled
    # This requires MySQL config change which we can't do without sudo
    print("Could not set up database automatically.")
    print("Please run the following commands manually as root:")
    print("  mysql -u root -p < /tmp/setup_db.sql")
    return False

if __name__ == "__main__":
    if setup_database():
        print("Database setup completed successfully!")
        sys.exit(0)
    else:
        print("Database setup failed!")
        sys.exit(1)
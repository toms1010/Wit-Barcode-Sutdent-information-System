#!/bin/bash
# Database Setup Script for WIT Barcode Student Information System
# Run this script as root or with sudo: sudo ./setup_database.sh

set -e

echo "=========================================="
echo "WIT Barcode System - Database Setup"
echo "=========================================="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run this script with sudo:"
    echo "  sudo ./setup_database.sh"
    exit 1
fi

# Resolve paths relative to this script so it works from any directory
# and on any machine (no hardcoded absolute paths).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_FILE="$SCRIPT_DIR/scripts/setup_database.sql"
PROJECT_DIR="$SCRIPT_DIR"

if [ ! -f "$SQL_FILE" ]; then
    echo "SQL file not found: $SQL_FILE"
    exit 1
fi

echo "Setting up database and user..."
mysql < "$SQL_FILE"

echo "=========================================="
echo "Database setup completed!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. cd \"$PROJECT_DIR\""
echo "2. source venv/bin/activate"
echo "3. python scripts/initialize_database.py"
echo "4. python main.py
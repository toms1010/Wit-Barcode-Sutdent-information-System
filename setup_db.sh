#!/bin/bash
# Database Setup Script for WIT Barcode Student Information System

echo "Setting up MySQL database..."

mysql -u root -p <<EOF
CREATE DATABASE IF NOT EXISTS wit_barcode_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'wit_user'@'localhost' IDENTIFIED BY 'wit_password';
GRANT ALL PRIVILEGES ON wit_barcode_system.* TO 'wit_user'@'localhost';
FLUSH PRIVILEGES;
EOF

echo "Database setup complete!"
# Database Setup Instructions

The MySQL `root` account on this machine uses `auth_socket` authentication, which
only works from a root shell. The application connects over the network stack,
so it must use its own account (`wit_user`) — that account must exist with the
correct password before the app can connect.

## Quick Setup

```bash
cd "/home/tommyp/Desktop/Desktop-app/WIT Barcode Student Information System"
sudo ./setup_database.sh
```

## Manual Setup

```bash
sudo mysql < scripts/setup_database.sql
```

Or interactively:

```bash
sudo mysql
```

```sql
CREATE DATABASE IF NOT EXISTS wit_barcode_system CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'wit_user'@'localhost' IDENTIFIED BY 'wit_password';
ALTER USER 'wit_user'@'localhost' IDENTIFIED BY 'wit_password';
GRANT ALL PRIVILEGES ON wit_barcode_system.* TO 'wit_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

### Why `ALTER USER` is required

`CREATE USER IF NOT EXISTS` is a **no-op when the account already exists**. If
`wit_user` is present but its password does not match `.env`, running only the
`CREATE USER` line leaves the bad password in place and the application still
fails with `1045 (28000): Access denied for user 'wit_user'@'localhost'`.
`ALTER USER ... IDENTIFIED BY ...` always applies the intended password, so the
script doubles as the repair step.

Both scripts are safe to re-run. **They never drop the database**, so existing
student, barcode and assessment records are preserved.

## After Database Setup

Once the account can authenticate, initialize the schema and seed data:

```bash
cd "/home/tommyp/Desktop/Desktop-app/WIT Barcode Student Information System"
venv/bin/python scripts/initialize_database.py
venv/bin/python -c "from database.connection import test_connection; print(test_connection())"
```

`test_connection()` printing `True` means the app can reach the database.

## Run the Application

```bash
venv/bin/python main.py
```

## Credentials Used

The `.env` file is configured with:
- Host: localhost
- Port: 3306
- Database: wit_barcode_system
- User: wit_user
- Password: (as set in `.env`)

## Troubleshooting

**`1045 (28000): Access denied for user 'wit_user'@'localhost'`**

The account exists but the password does not match `.env`. Re-apply the
password as an administrator:

```bash
sudo mysql -e "ALTER USER 'wit_user'@'localhost' IDENTIFIED BY 'wit_password';
               FLUSH PRIVILEGES;"
```

Confirm the account and its host entries:

```bash
sudo mysql -e "SELECT User, Host FROM mysql.user WHERE User='wit_user';"
```

If only a `127.0.0.1` row is listed and you connect over TCP, run
`scripts/setup_database.sql`, which grants the account for both hosts.

**`Access denied for user 'root'@'localhost'`**

The setup was not run with `sudo`. The root account uses socket authentication
and cannot be used by the application. Do not set `DB_USER=root` in `.env`; use
`wit_user`.

**`Unknown database 'wit_barcode_system'`**

The database was not created. Run the setup script with `sudo`.

**Application shows "Database is not reachable"**

The connection layer failed after starting. Use the **Retry** button in the
dialog, which resets the pool and attempts a fresh connection. Repeated
failures are throttled so the app does not hammer the server.

**Changing credentials**

Update both `.env` and `scripts/setup_database.sql`, then re-run the setup with
`sudo`. Keep `.env` out of version control (it is already in `.gitignore`).

# Sua+ Database Backup Script

BACKUP_DIR="/var/backups/suaplus"
DB_NAME="suaplus"
DB_USER="suaplus_dev"
DATE=$(date +%Y-%m-%d_%H-%M-%S)
FILENAME="$BACKUP_DIR/suaplus_$DATE.sql.gz"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Dump the database and compress it
pg_dump -U "$DB_USER" -h localhost -d "$DB_NAME" | gzip > "$FILENAME"

# Delete backups older than 30 days
find "$BACKUP_DIR" -type f -name "*.sql.gz" -mtime +30 -delete

echo "Backup completed: $FILENAME"
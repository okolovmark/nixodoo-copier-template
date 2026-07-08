# shellcheck shell=bash
# Interactive .aws/credentials generator (no-op if it already exists).
set -e
umask 077    # credentials: dir 700, file 600
if [ ! -f "$PWD/.aws/credentials" ]; then
    echo "Creating AWS configuration file..."
    echo "Request key and secret for AWS from your Manager"
    echo "(Or take it from your ~/.aws/credentials file)"
    mkdir -p "$PWD/.aws"
    read -r -p "AWS Access Key ID: " aws_access_key_id
    read -r -s -p "AWS Secret Access Key: " aws_secret_access_key
    echo
    {
        echo "[default]"
        echo "aws_access_key_id = $aws_access_key_id"
        echo "aws_secret_access_key = $aws_secret_access_key"
    } > "$PWD/.aws/credentials"
    echo "AWS configuration file created successfully"
fi

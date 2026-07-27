# Gmail Auto Keep-Alive

A simple Python utility to automatically sign into Gmail accounts periodically so they stay active.

## Setup

1. Install Python 3.9+ and required packages:
   pip install -r requirements.txt

2. Create a configuration file (e.g., `config.yaml`) with your account credentials. **Never commit sensitive data to version control.** See `config.yaml.example` for a template.

3. Schedule the script to run every 24 hours using cron or any task scheduler.

## Usage

```bash
python keep_alive.py --config config.yaml
```

## Security

- Use Google OAuth2 with refresh tokens for production usage.
- Store credentials securely (e.g., environment variables, secret manager).
- Rotate passwords regularly.


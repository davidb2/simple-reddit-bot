Example `.env` file:

```bash
# See https://www.reddit.com/prefs/apps.
CLIENT_SECRET=XXXXXXXXXXXX
CLIENT_ID=YYYYYYYYYYYYY
USERNAME=<reddit bot username>
PASSWORD=<reddit bot password>
VERSION=1.0.0
```

Example `params.yaml` file:

```yaml
dry_run: false
pattern: "\\bdog\\b"
reply: "woof"
subreddit: cats
timeout: PT10M
```

Example run:
```bash
./main --params params.yaml
```
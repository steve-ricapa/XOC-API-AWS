# AGENTS.md

## Repo shape that matters

- This is a multi-stack Serverless repo. Active service configs at the root are:
  - `serverless.shared.js`
  - `serverless.tickets.js`
  - `serverless.auth.js`
  - `serverless.chat.js`
  - `serverless.ops.js`
  - `serverless.tenant.js`
  - `serverless.admin.js`
  - `serverless.reports.js`
- `README.md` and `MULTI_SERVICE_DEPLOY.md` still describe the older 7-stack split in places; trust the root `serverless*.js` files over prose.

## Deploy commands

- Use `package.json` scripts instead of guessing raw `serverless` commands.
- Prod deploys:
  - `npm run deploy:shared:prod`
  - `npm run deploy:tickets:prod`
  - `npm run deploy:auth:prod`
  - `npm run deploy:chat:prod`
  - `npm run deploy:tenant:prod`
  - `npm run deploy:admin:prod`
  - `npm run deploy:ops:prod`
  - `npm run deploy:reports:prod`
- If infra references changed, deploy order matters:
  1. `shared`
  2. `tickets`
  3. `auth`
  4. `chat`
  5. `tenant`
  6. `admin`
  7. `ops`
  8. `reports`

## SSH to prod EC2

- Deploys are typically run from the EC2, not from the local machine.
- Current public EC2 IP: `13.223.193.42`
- User: `ubuntu`
- Key: `~/.ssh/xoc-ec2`
- Key passphrase: `pepe123`
- Repo path on EC2: `~/XOC_AWS`

- Non-interactive SSH pattern that works from OpenCode/Git Bash:

```bash
printf '#!/bin/bash\necho "pepe123"\n' > /tmp/ssh-askpass.sh
chmod +x /tmp/ssh-askpass.sh
eval $(ssh-agent -s) > /dev/null
SSH_ASKPASS_REQUIRE=force SSH_ASKPASS=/tmp/ssh-askpass.sh ssh-add ~/.ssh/xoc-ec2 </dev/null 2>&1
ssh -A -o ConnectTimeout=20 -o ServerAliveInterval=15 ubuntu@13.223.193.42
```

- Single-command deploy example:

```bash
printf '#!/bin/bash\necho "pepe123"\n' > /tmp/ssh-askpass.sh && chmod +x /tmp/ssh-askpass.sh && eval $(ssh-agent -s) > /dev/null && SSH_ASKPASS_REQUIRE=force SSH_ASKPASS=/tmp/ssh-askpass.sh ssh-add ~/.ssh/xoc-ec2 </dev/null 2>&1 && ssh -A -o ConnectTimeout=20 -o ServerAliveInterval=15 ubuntu@13.223.193.42 "cd ~/XOC_AWS && git pull origin main && npm run deploy:ops:prod 2>&1"
```

## EC2 deploy gotchas

- The repo remote on EC2 should be HTTPS for pulls from the public repo. If `git pull` fails with `github.com: Permission denied (publickey)`, run:

```bash
git remote set-url origin https://github.com/steve-ricapa/XOC-API-AWS.git
```

- The EC2 needs `python3.11` installed for `serverless-python-requirements`. Verify with `python3.11 --version`.
- If packaging behavior changed, clear stale artifacts before redeploying:

```bash
rm -rf .serverless ~/.cache/serverless-python-requirements
```

## Packaging / dependency quirks

- Packaging behavior is defined in `serverless/services/lib/common.js`; trust that file over older docs.
- Important current settings:
  - `dockerizePip: false`
  - `pipCmdExtraArgs: ['--only-binary', ':all:']`
  - `slim: true`
  - `noDeploy: ['boto3', 'botocore', 's3transfer', 'jmespath']`
  - package patterns exclude `experiments/**`
- If a Lambda suddenly balloons in size, check `common.js` first and inspect `.serverless/requirements` on EC2.

## Route ownership that is easy to guess wrong

- Use `/dashboard/*` for frontend operational screens.
- Use `/integrations/*` for integration CRUD/settings.
- Finding detail lives under `GET /findings/{findingId}` and scan findings under `/scans/{scanSummaryId}/findings`.
- Reports are their own service now: `serverless.reports.js`, not part of `ops`.

## Docs status

- `docs/api-guide.md` is useful but can lag behind deployed reality.
- `docs/frontend-api-consumption.md` is the best current frontend-oriented contract summary.

# OpenClaw Connector

The **OpenClaw Connector** (`connector`) is a standalone process that allows you to control your local ComfyUI instance remotely via chat platforms like **Telegram**, **Discord**, **LINE**, **WhatsApp**, **WeChat Official Account**, and **KakaoTalk (Kakao i Open Builder)**.

## How It Works

The connector runs alongside ComfyUI on your machine.

1. It connects outbound to Telegram/Discord (polling/gateway).
2. LINE/WhatsApp/WeChat/KakaoTalk use inbound webhooks (HTTPS required).
3. It talks to ComfyUI via `localhost`.
4. It relays commands and status updates securely.

**Security**:

- **Transport Model**: Telegram/Discord are outbound. LINE/WhatsApp/WeChat/KakaoTalk/Slack require inbound HTTPS webhook endpoints.
- **Allowlist/Trust Model**: Allowlists define trusted senders/channels. Non-allowlisted senders are treated as untrusted (for example, `/run` is approval-routed instead of auto-executed).
- **Strict Profile Gate**: In `public` deployment or `hardened` runtime posture, enabling connector ingress without platform allowlist coverage is fail-closed at startup/deployment checks.
- **Local Secrets**: Bot tokens are stored in your local environment, never sent to ComfyUI.
- **Admin Boundary**: Control-plane actions call admin endpoints on the local OpenClaw server and require connector-side admin token configuration for admin command paths.

### Installation and callback contract baseline

OpenClaw now includes a platform-agnostic baseline for multi-workspace connector lifecycle and interactive callback security:

- installation registry stores normalized records:
  - `platform`, `workspace_id`, `installation_id`, `token_refs`, `status`, `updated_at`
- token material is kept in encrypted server-side secret storage; registry and diagnostics expose token references only
- workspace resolution is fail-closed on missing/ambiguous/inactive/stale bindings
- interactive callback contract enforces signed envelope checks, timestamp window, payload-hash validation, replay/idempotency guardrails, and command-policy mapping (`public`/`run`/`admin`) with explicit force-approval outcomes for untrusted `run` callbacks

Admin diagnostics APIs:

- `GET /openclaw/connector/installations`
- `GET /openclaw/connector/installations/{installation_id}`
- `GET /openclaw/connector/installations/resolve?platform=<platform>&workspace_id=<workspace_id>`
- `GET /openclaw/connector/installations/audit`

### Multi-tenant boundary behavior

When backend multi-tenant mode is enabled (`OPENCLAW_MULTI_TENANT_ENABLED=1`):

- installation records are tenant-owned (`tenant_id`) and diagnostics are tenant-scoped
- resolution rejects cross-tenant matches fail-closed (`tenant_mismatch` path)
- admin diagnostics calls can pass tenant context via token context and/or `X-OpenClaw-Tenant-Id` (or your configured `OPENCLAW_TENANT_HEADER`)
- missing tenant context currently falls back to `default` tenant for compatibility unless stricter caller paths are used

## Supported Platforms

- **Telegram**: Long-polling (instant response).
- **Discord**: Gateway WebSocket (instant response).
- **LINE**: Webhook (requires inbound HTTPS).
- **WhatsApp**: Webhook (requires inbound HTTPS).
- **WeChat Official Account**: Webhook (requires inbound HTTPS).
- **KakaoTalk (Kakao i Open Builder)**: Webhook (requires inbound HTTPS).
- **Slack (Events API)**: Webhook (requires inbound HTTPS).

## Setup

### 1. Requirements

- Python 3.10+
- `aiohttp` (installed with ComfyUI-OpenClaw)

### 2. Configuration

Set the following environment variables (or put them in a `.env` file if you use a loader):

**Common:**

- `OPENCLAW_CONNECTOR_URL`: URL of your ComfyUI (default: `http://127.0.0.1:8188`)
- `OPENCLAW_CONNECTOR_DEBUG`: Set to `1` for verbose logs.
- `OPENCLAW_CONNECTOR_ADMIN_USERS`: Comma-separated list of user IDs allowed to run admin commands (for example `/stop`, approvals, schedules). Admin users are also treated as trusted senders for `/run`.
- `OPENCLAW_CONNECTOR_ADMIN_TOKEN`: Admin token sent to OpenClaw (`X-OpenClaw-Admin-Token`).
- `OPENCLAW_LOG_TRUNCATE_ON_START`: Optional backend runtime flag. Set `1` to clear `openclaw.log` once at backend startup to avoid stale-history noise in UI log panels.
- `OPENCLAW_MULTI_TENANT_ENABLED`: Optional backend mode toggle. If `1`, connector diagnostics and installation resolution become tenant-scoped.
- `OPENCLAW_TENANT_HEADER`: Optional tenant header key (default `X-OpenClaw-Tenant-Id`) used when calling tenant-scoped backend APIs.

**Admin token behavior:**

- Connector admin command paths require `OPENCLAW_CONNECTOR_ADMIN_TOKEN` to be set in connector runtime.
- If the OpenClaw server has `OPENCLAW_ADMIN_TOKEN` configured, `OPENCLAW_CONNECTOR_ADMIN_TOKEN` must match it or admin calls return HTTP 403.
- Without `OPENCLAW_CONNECTOR_ADMIN_TOKEN`, admin command flows (`/approve`, `/reject`, `/trace`, schedules) are blocked by connector policy before upstream calls.

**Telegram:**

- `OPENCLAW_CONNECTOR_TELEGRAM_TOKEN`: Your Bot Token (from @BotFather).
- `OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_USERS`: Comma-separated list of User IDs (e.g. `123456, 789012`).
- `OPENCLAW_CONNECTOR_TELEGRAM_ALLOWED_CHATS`: Comma-separated list of Chat/Group IDs.

**Discord:**

- `OPENCLAW_CONNECTOR_DISCORD_TOKEN`: Your Bot Token (from Discord Developer Portal).
- `OPENCLAW_CONNECTOR_DISCORD_ALLOWED_USERS`: Comma-separated User IDs.
- `OPENCLAW_CONNECTOR_DISCORD_ALLOWED_CHANNELS`: Comma-separated Channel IDs the bot should listen in.

**LINE:**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_LINE_CHANNEL_SECRET`: LINE Channel Secret.
- `OPENCLAW_CONNECTOR_LINE_CHANNEL_ACCESS_TOKEN`: LINE Channel Access Token.
- `OPENCLAW_CONNECTOR_LINE_ALLOWED_USERS`: Comma-separated User IDs (e.g. `U1234...`).
- `OPENCLAW_CONNECTOR_LINE_ALLOWED_GROUPS`: Comma-separated Group IDs (e.g. `C1234...`).
- `OPENCLAW_CONNECTOR_LINE_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_LINE_PORT`: Port (default `8099`).
- `OPENCLAW_CONNECTOR_LINE_PATH`: Webhook path (default `/line/webhook`).

**WhatsApp:**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_WHATSAPP_ACCESS_TOKEN`: Cloud API access token.
- `OPENCLAW_CONNECTOR_WHATSAPP_VERIFY_TOKEN`: Webhook verify token (used during setup).
- `OPENCLAW_CONNECTOR_WHATSAPP_APP_SECRET`: App secret for signature verification (recommended).
- `OPENCLAW_CONNECTOR_WHATSAPP_PHONE_NUMBER_ID`: Phone number ID used for outbound messages.
- `OPENCLAW_CONNECTOR_WHATSAPP_ALLOWED_USERS`: Comma-separated sender `wa_id` values (phone numbers).
- `OPENCLAW_CONNECTOR_WHATSAPP_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_WHATSAPP_PORT`: Port (default `8098`).
- `OPENCLAW_CONNECTOR_WHATSAPP_PATH`: Webhook path (default `/whatsapp/webhook`).

**WeChat Official Account:**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_WECHAT_TOKEN`: WeChat server verification token (**required** for adapter startup).
- `OPENCLAW_CONNECTOR_WECHAT_APP_ID`: Official Account AppID (required for proactive outbound message API calls).
- `OPENCLAW_CONNECTOR_WECHAT_APP_SECRET`: Official Account AppSecret (required for proactive outbound message API calls).
- `OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS`: Comma-separated OpenID allowlist. Non-allowlisted users are treated as untrusted and routed through approval semantics for sensitive actions.
- `OPENCLAW_CONNECTOR_WECHAT_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_WECHAT_PORT`: Port (default `8097`).
- `OPENCLAW_CONNECTOR_WECHAT_PATH`: Webhook path (default `/wechat/webhook`).

**KakaoTalk (Kakao i Open Builder):**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_KAKAO_ENABLED`: Set to `true` to enable Kakao webhook adapter.
- `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS`: Comma-separated Kakao user IDs (`userRequest.user.id` / botUserKey). Non-allowlisted users are treated as untrusted and sensitive actions require approval.
- `OPENCLAW_CONNECTOR_KAKAO_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_KAKAO_PORT`: Port (default `8096`).
- `OPENCLAW_CONNECTOR_KAKAO_PATH`: Webhook path (default `/kakao/webhook`).

**Slack (Events API):**

*(Requires Inbound Connectivity - see below)*

- `OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN`: Bot User OAuth Token (`xoxb-...`).
- `OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET`: Signing Secret (from App Credentials).
- `OPENCLAW_CONNECTOR_SLACK_ALLOWED_USERS`: Comma-separated user IDs (e.g. `U12345, U67890`).
- `OPENCLAW_CONNECTOR_SLACK_ALLOWED_CHANNELS`: Comma-separated channel IDs (e.g. `C12345`).
- `OPENCLAW_CONNECTOR_SLACK_BIND`: Host to bind (default `127.0.0.1`).
- `OPENCLAW_CONNECTOR_SLACK_PORT`: Port (default `8095`).
- `OPENCLAW_CONNECTOR_SLACK_PATH`: Webhook path (default `/slack/events`).
- `OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION`: `true` (default) to require `@Bot` mention in public channels.
- `OPENCLAW_CONNECTOR_SLACK_REPLY_IN_THREAD`: `true` (default) to reply in threads.

**Image Delivery:**

- `OPENCLAW_CONNECTOR_PUBLIC_BASE_URL`: Public HTTPS URL of your connector (e.g. `https://your-tunnel.example.com`). Required for sending images.
- `OPENCLAW_CONNECTOR_MEDIA_PATH`: URL path for serving temporary media (default `/media`).
- `OPENCLAW_CONNECTOR_MEDIA_TTL_SEC`: Image expiry in seconds (default `300`).
- `OPENCLAW_CONNECTOR_MEDIA_MAX_MB`: Max image size in MB (default `8`).

> **Note:** Media URLs are signed with a secret derived from `OPENCLAW_CONNECTOR_ADMIN_TOKEN` or a random key.
> To ensure URLs remain valid after connector restarts, **you must set `OPENCLAW_CONNECTOR_ADMIN_TOKEN`**.
> LINE and WhatsApp also **require** `public_base_url` to be HTTPS.
> WeChat currently supports text-first control. Image/media upload delivery is not implemented in phase 1.
> Kakao currently supports text-first control and quick replies. Rich media delivery is not enabled in the default Kakao webhook flow.
> Slack supports text responses and image uploads (via `files.upload` API).

### Command authorization policy

Connector commands are evaluated through a centralized authorization policy with three command classes:

- `public`: low-risk status/help style commands
- `run`: execution commands such as `/run` (still subject to trust/approval behavior)
- `admin`: sensitive commands such as `/trace`, `/approvals`, `/approve`, `/reject`, and schedule controls

Default behavior:

- If no explicit allow-from list is configured for a command class, class-level defaults apply.
- `admin` commands require the sender to be in `OPENCLAW_CONNECTOR_ADMIN_USERS`.
- `public` and `run` commands still pass through each platform adapter's trust/allowlist checks.

Optional policy controls:

- `OPENCLAW_COMMAND_OVERRIDES`: JSON object mapping command name to class (`public`, `run`, `admin`).
- `OPENCLAW_COMMAND_ALLOW_FROM_PUBLIC`: comma-separated sender IDs.
- `OPENCLAW_COMMAND_ALLOW_FROM_RUN`: comma-separated sender IDs.
- `OPENCLAW_COMMAND_ALLOW_FROM_ADMIN`: comma-separated sender IDs.

Normalization rules:

- Command keys in `OPENCLAW_COMMAND_OVERRIDES` are normalized to lowercase.
- Missing leading `/` is added automatically.

Example:

```bash
OPENCLAW_COMMAND_OVERRIDES='{"run":"admin","/status":"public"}'
OPENCLAW_COMMAND_ALLOW_FROM_ADMIN=alice_id,bob_id
OPENCLAW_COMMAND_ALLOW_FROM_RUN=alice_id,ops_bot_id
```

If a class-level `OPENCLAW_COMMAND_ALLOW_FROM_*` list is set and non-empty, only listed IDs can run that class.

### 3. Usage

#### Running the Connector

```bash
python -m connector
```

#### LINE Webhook Setup

Unlike Telegram/Discord which pull messages, LINE pushes webhooks to your connector.
Since the connector runs on `localhost` (default port 8099), you must expose it to the internet securely.

**Option A: Cloudflare Tunnel (Recommended)**

1. Install `cloudflared`.
2. Run: `cloudflared tunnel --url http://127.0.0.1:8099`
3. Copy the generated URL (e.g. `https://random-name.trycloudflare.com`).
4. In LINE Developers Console > Messaging API > Webhook settings:
   - Set URL to `https://<your-tunnel>/line/webhook` (or your custom path).
   - Enable "Use webhook".

**Option B: Reverse Proxy (Nginx/Caddy)**

- Configure your proxy to forward HTTPS traffic to `127.0.0.1:8099`.

#### WhatsApp Webhook Setup

WhatsApp Cloud API delivers webhooks to your connector. You must expose it via HTTPS.

1. Create a Meta app and add the WhatsApp product.
2. Add a phone number and note its **Phone Number ID**.
3. Configure the webhook URL: `https://<your-public-host>/whatsapp/webhook`.
4. Set the webhook **Verify Token** to match `OPENCLAW_CONNECTOR_WHATSAPP_VERIFY_TOKEN`.
5. Subscribe to `messages` events.
6. Ensure `OPENCLAW_CONNECTOR_PUBLIC_BASE_URL` is an HTTPS URL so media can be delivered.

If you run locally, use a secure tunnel (Cloudflare Tunnel or ngrok) and point it to `http://127.0.0.1:8098`.

#### WeChat Official Account Webhook Setup (Detailed)

WeChat Official Account pushes webhook requests to your connector. You must expose the WeChat endpoint publicly over HTTPS.

1. Prepare the required environment variables:

   ```bash
   OPENCLAW_CONNECTOR_WECHAT_TOKEN=replace-with-your-wechat-token
   OPENCLAW_CONNECTOR_WECHAT_APP_ID=wx1234567890abcdef
   OPENCLAW_CONNECTOR_WECHAT_APP_SECRET=replace-with-app-secret
   OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS=openid_1,openid_2
   OPENCLAW_CONNECTOR_WECHAT_BIND=127.0.0.1
   OPENCLAW_CONNECTOR_WECHAT_PORT=8097
   OPENCLAW_CONNECTOR_WECHAT_PATH=/wechat/webhook
   ```

2. Start the connector:

   ```bash
   python -m connector
   ```

3. Expose the local webhook service to HTTPS (Cloudflare Tunnel or reverse proxy):
   - local upstream: `http://127.0.0.1:8097`
   - public path: `/wechat/webhook`
   - expected public URL: `https://<your-public-host>/wechat/webhook`

4. In WeChat Official Account backend (Developer settings / server config), configure:
   - URL: `https://<your-public-host>/wechat/webhook`
   - Token: same value as `OPENCLAW_CONNECTOR_WECHAT_TOKEN`
   - EncodingAESKey: set according to your WeChat backend requirement
   - Message encryption mode: use plaintext/compatible mode for this adapter path

5. Save/submit server config. WeChat will call your endpoint with verification query parameters.
   - expected success behavior: connector returns `echostr` and logs verification success
   - expected failure behavior: `403 Verification failed` if token/signature mismatches

6. Functional test:
   - follow your Official Account with a test user
   - send `/help` or `/status`
   - verify connector receives command and returns text reply

7. Verify trusted/untrusted behavior:
   - if sender OpenID is in `OPENCLAW_CONNECTOR_WECHAT_ALLOWED_USERS`, `/run` can execute directly (subject to trust/approval policy and command policy)
   - if not allowlisted, sensitive actions are routed to approval flow

**WeChat-specific notes:**

- The adapter validates WeChat signature on every request and applies replay/timestamp checks.
- Timestamp skew outside policy window is rejected (`403 Stale Request`).
- XML payload parsing is bounded (size/depth/field caps) and fails closed on parser budget violations.
- Runtime XML security gate is fail-closed: unsafe/missing parser baseline blocks ingress startup.
- Current command surface is text-first. Unsupported message/event types are ignored with success response.
- Proactive outbound API messaging requires both `OPENCLAW_CONNECTOR_WECHAT_APP_ID` and `OPENCLAW_CONNECTOR_WECHAT_APP_SECRET`.

#### KakaoTalk (Kakao i Open Builder) Webhook Setup (Detailed)

Kakao i Open Builder sends webhook requests to your connector Skill endpoint. You must expose the Kakao endpoint publicly over HTTPS.

1. Prepare the required environment variables:

   ```bash
   OPENCLAW_CONNECTOR_KAKAO_ENABLED=true
   OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS=kakao_user_id_1,kakao_user_id_2
   OPENCLAW_CONNECTOR_KAKAO_BIND=127.0.0.1
   OPENCLAW_CONNECTOR_KAKAO_PORT=8096
   OPENCLAW_CONNECTOR_KAKAO_PATH=/kakao/webhook
   ```

2. Start the connector:

   ```bash
   python -m connector
   ```

3. Expose the local webhook service to HTTPS (Cloudflare Tunnel or reverse proxy):
   - local upstream: `http://127.0.0.1:8096`
   - public path: `/kakao/webhook`
   - expected public URL: `https://<your-public-host>/kakao/webhook`

4. In Kakao i Open Builder:
   - create/select your bot
   - create/select a Skill
   - set Skill server URL to `https://<your-public-host>/kakao/webhook`
   - deploy/publish the Skill scenario that calls this Skill endpoint

5. Functional test:
   - chat with your Kakao bot
   - send `/help` or `/status`
   - verify connector receives command and returns a SkillResponse (`version: 2.0`)

6. Verify trusted/untrusted behavior:
   - if sender `userRequest.user.id` is in `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS`, `/run` can execute directly (subject to trust/approval policy and command policy)
   - if not allowlisted, sensitive actions are routed to approval flow

7. Optional first-time allowlist bootstrap:
   - temporarily leave `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS` empty
   - send a test message and check logs for `Untrusted Kakao message from user=<id>`
   - add that ID to allowlist and restart connector

**Kakao-specific notes:**

- Adapter is disabled unless `OPENCLAW_CONNECTOR_KAKAO_ENABLED=true`.
- Kakao webhook ingress is `POST` only on `OPENCLAW_CONNECTOR_KAKAO_PATH`.
- Replay protection is enabled: identical payloads within the replay window are acknowledged and not re-executed.
- Kakao command requests are normalized from:
  - `userRequest.user.id` -> `sender_id`
  - `userRequest.utterance` -> command text
- Response format follows Kakao SkillResponse v2.0 with text-first output and optional quick replies.
- If `aiohttp` is missing, the adapter is skipped at startup.

#### Slack Webhook Setup (Detailed)

Slack uses the Events API webhook mode in OpenClaw. You must expose the endpoint publicly over HTTPS.

1. **Create the Slack App**
   - Go to [api.slack.com/apps](https://api.slack.com/apps).
   - Create a new app (From scratch) and select your workspace.
   - In **Basic Information**, copy the **Signing Secret**.

2. **Configure OAuth Scopes and install**
   - Go to **OAuth & Permissions**.
   - Add bot scopes:
     - `chat:write`
     - `files:write`
     - `app_mentions:read`
     - `im:history` (DM support)
     - `channels:history` (public channel messages)
     - `groups:history` (private channel messages)
   - Click **Install to Workspace**.
   - Copy the **Bot User OAuth Token** (`xoxb-...`).

3. **Configure connector environment variables**

   ```bash
   OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN=xoxb-your-token
   OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET=your-signing-secret
   OPENCLAW_CONNECTOR_SLACK_ALLOWED_USERS=U12345,U67890
   OPENCLAW_CONNECTOR_SLACK_ALLOWED_CHANNELS=C12345
   OPENCLAW_CONNECTOR_SLACK_BIND=127.0.0.1
   OPENCLAW_CONNECTOR_SLACK_PORT=8095
   OPENCLAW_CONNECTOR_SLACK_PATH=/slack/events
   OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION=true
   OPENCLAW_CONNECTOR_SLACK_REPLY_IN_THREAD=true
   OPENCLAW_CONNECTOR_ADMIN_TOKEN=replace-with-openclaw-admin-token
   ```

   Notes:
   - `OPENCLAW_CONNECTOR_ADMIN_TOKEN` must match server `OPENCLAW_ADMIN_TOKEN` if server-side admin token is enabled.
   - Slack ingress is fail-closed: invalid/missing signature, stale timestamp, and replayed events are rejected.

4. **Start connector and expose webhook endpoint**
   - Start connector: `python -m connector`
   - Expose local endpoint to public HTTPS (Cloudflare Tunnel/ngrok/reverse proxy):
     - local upstream: `http://127.0.0.1:8095`
     - public URL: `https://<public-host>/slack/events`

5. **Enable Event Subscriptions**
   - Go to **Event Subscriptions** and enable events.
   - Set **Request URL** to `https://<public-host>/slack/events`.
   - Slack sends `url_verification`; connector responds automatically.
   - Add bot events:
     - `app_mention`
     - `message.channels`
     - `message.groups`
     - `message.im`

6. **Invite and validate**
   - Invite the app to target channels: `/invite @YourBot`.
   - In channel: `@YourBot /status` (when `OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION=true`).
   - In DM: `/help`.
   - Verify connector logs show signed ingress accepted and replies delivered.

7. **Security checklist before production**
   - Keep `OPENCLAW_CONNECTOR_SLACK_ALLOWED_USERS`/`OPENCLAW_CONNECTOR_SLACK_ALLOWED_CHANNELS` restricted.
   - Keep `OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION=true` unless intentionally running command-style channels.
   - Rotate Slack bot token/signing secret on incident response.
   - Do not expose connector without HTTPS termination.

#### Slack Socket Mode Setup (Optional)

Use Socket Mode when you cannot expose a public HTTPS webhook endpoint.

1. **Enable Socket Mode in Slack**
   - Open your Slack App settings.
   - Go to **Socket Mode** and enable it.
   - Create an App-Level Token (`xapp-...`) with `connections:write`.

2. **Configure connector**

   ```bash
   OPENCLAW_CONNECTOR_SLACK_MODE=socket
   OPENCLAW_CONNECTOR_SLACK_APP_TOKEN=xapp-your-token
   # Bot token + signing secret are still required for parity and safety checks
   OPENCLAW_CONNECTOR_SLACK_BOT_TOKEN=xoxb-your-token
   OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET=your-signing-secret
   ```

3. **Start connector**
   - `python -m connector`
   - Expect log: `Slack Socket Mode connected.`

Notes:
- Socket Mode uses outbound WebSocket, so `OPENCLAW_CONNECTOR_SLACK_BIND`, `OPENCLAW_CONNECTOR_SLACK_PORT`, and `OPENCLAW_CONNECTOR_SLACK_PATH` are ignored in this mode.
- Startup is fail-closed if `OPENCLAW_CONNECTOR_SLACK_APP_TOKEN` is missing or does not start with `xapp-`.

## Commands

**General:**

| Command | Description |
| :--- | :--- |
| `/status` | Check ComfyUI system status, logs, and queue size. |
| `/jobs` | View active jobs and queue summary. |
| `/history <id>` | View details of a finished job. |
| `/help` | Show available commands. |
| `/run <template> [k=v] [--approval]` | Submit a job. Use `--approval` to request approval gate instead of creating job immediately. |
| `/stop` | **Global Interrupt**: Stop all running generations. |

**Admin Only:**
*(Requires User ID in `OPENCLAW_CONNECTOR_ADMIN_USERS`)*

| Command | Description |
| :--- | :--- |
| `/trace <id>` | View raw execution logs/trace for a job. |
| `/approvals` | List pending approvals. |
| `/approve <id>` | Approve a pending request (triggers execution immediately). |
| `/reject <id> [reason]` | Reject a workflow. |
| `/schedules` | List schedules. |
| `/schedule run <id>` | Trigger a schedule immediately. |

## Usage Examples

### Approval Gated Run

1. **Submission (Admin)**:

   ```
   User: /run my-template steps=20 --approval
   Bot:  [Approval Requested]
         ID: apr_12345
         Trace: ...
         Expires: 2026-02-07T12:00:00Z
   ```

2. **Approval (Admin)**:

   ```
   User: /approve apr_12345
   Bot:  [Approved] apr_12345
         Executed: p_98765
   ```

### Common Failure Modes

- **(Not Executed)**:
  - If `/approve` returns `[Approved] ... (Not Executed)`, it means the request state was updated to Approved, but the job could not be autostarted.
  - **Reason**: Backend might lack a submit handler for this trigger type, or `auto_execute` failed. Check `openclaw` server logs.
  - **Action**: Manually run the job using the template/inputs from the approval request.

- **Access Denied**:
  - Sender is not in `OPENCLAW_CONNECTOR_ADMIN_USERS`.
  - Fix: Add ID to `.env` and restart connector.

- **HTTP 403 (Admin Token)**:
  - Connector has the right user allowlist, but the upstream OpenClaw server rejected the Admin Token.
  - Fix: Ensure `OPENCLAW_CONNECTOR_ADMIN_TOKEN` matches the server's `OPENCLAW_ADMIN_TOKEN`.

- **Kakao requests not arriving**:
  - `OPENCLAW_CONNECTOR_KAKAO_ENABLED` is not `true`, or Kakao Skill URL/path does not match `OPENCLAW_CONNECTOR_KAKAO_PATH`.
  - Fix: set `OPENCLAW_CONNECTOR_KAKAO_ENABLED=true`, verify public HTTPS URL, and confirm Skill URL exactly matches `/kakao/webhook` (or your custom path).

- **Kakao returns empty/fallback response**:
  - Payload is malformed (missing `userRequest.user.id` / `userRequest.utterance`) or the adapter rejected malformed JSON.
  - Fix: validate Skill request payload shape in Open Builder test console and check connector logs for `Bad JSON` / payload errors.

- **Kakao `/run` always goes to approval**:
  - Sender is not in `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS` (or allowlist is empty).
  - Fix: capture `userRequest.user.id` from logs, add it to `OPENCLAW_CONNECTOR_KAKAO_ALLOWED_USERS`, restart connector.

- **Slack Event Subscriptions verification fails**:
  - Request URL/path mismatch, connector not reachable, or `OPENCLAW_CONNECTOR_SLACK_SIGNING_SECRET` is wrong.
  - Fix: confirm public URL points to `/slack/events`, verify tunnel/proxy routes to `127.0.0.1:8095`, and re-check Signing Secret.

- **Slack commands ignored in channels**:
  - `OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION=true` and message does not mention the bot.
  - Fix: mention bot explicitly (`@Bot /status`) or set `OPENCLAW_CONNECTOR_SLACK_REQUIRE_MENTION=false` if policy allows.

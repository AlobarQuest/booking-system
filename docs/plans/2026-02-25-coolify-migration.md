# Coolify Migration Implementation Plan

> **For Claude:** This is a human-executed infrastructure runbook. Most steps require interacting with external web UIs (Hetzner, Coolify, Google Cloud Console) and a remote server via SSH. Guide the user through each task interactively. Subagent execution is not applicable for the server/UI steps, but Claude can prepare commands, verify config files, and confirm results.

**Goal:** Move the booking app from Fly.io to a self-hosted Coolify instance on Hetzner, establishing a reusable platform for future apps at a fixed ~$4.50/month regardless of how many apps are added.

**Architecture:** Hetzner CAX11 VPS (ARM, 2 vCPU, 4GB RAM) running Coolify, which manages Docker deployments, HTTPS certificates, and reverse proxying. The booking app deploys from the GitHub repo via Coolify's GitHub integration. SQLite data persists in a Docker volume mounted at `/data`.

**Tech Stack:** Hetzner Cloud, Coolify (open source PaaS), Docker, Let's Encrypt (HTTPS), GitHub Actions-free deploys via Coolify webhooks.

**Important notes:**
- Steps marked 🖥️ require SSH into the VPS
- Steps marked 🌐 require a web browser (Hetzner console, Coolify UI, Google Console)
- Steps marked 💻 run locally on your machine
- The Google OAuth redirect URI must be updated when the domain changes — do NOT skip Task 6

---

## Task 1: Create a Hetzner account and provision the VPS

**Time: ~15 minutes**

🌐 **Step 1: Create Hetzner account**

Go to https://www.hetzner.com/cloud and sign up. You will need a credit card. New accounts get €20 in free credits (roughly 4 months of a CAX11).

🌐 **Step 2: Create an SSH key (if you don't have one)**

💻 On your local machine:
```bash
# Check if you already have one
ls ~/.ssh/id_ed25519.pub

# If not, generate one
ssh-keygen -t ed25519 -C "your@email.com"
# Accept defaults, set a passphrase if you want

# Copy the public key to clipboard
cat ~/.ssh/id_ed25519.pub
```

🌐 **Step 3: Add SSH key to Hetzner**

In Hetzner Cloud Console:
- Go to **Security → SSH Keys → Add SSH Key**
- Paste your public key
- Name it something like `my-laptop`

🌐 **Step 4: Create the server**

In Hetzner Cloud Console → **Create Server**:
- **Location:** Ashburn, VA (US East) — closest to your users if they're in the US
- **Image:** Ubuntu 24.04
- **Type:** Shared vCPU → ARM64 → **CAX11** (2 vCPU, 4GB RAM, ~$4.50/month)
- **SSH Keys:** Select the key you just added
- **Name:** `coolify` or similar
- Click **Create & Buy Now**

🌐 **Step 5: Note the server's IP address**

After creation, copy the IPv4 address. You'll need it throughout this guide. Call it `YOUR_SERVER_IP`.

---

## Task 2: Initial server setup

**Time: ~10 minutes**

🖥️ **Step 1: SSH into the server**

💻 On your local machine:
```bash
ssh root@YOUR_SERVER_IP
```

🖥️ **Step 2: Update the system**

```bash
apt update && apt upgrade -y
```

🖥️ **Step 3: Set the hostname (optional but helpful)**

```bash
hostnamectl set-hostname coolify
```

🖥️ **Step 4: Configure the firewall**

Coolify requires specific ports. Run:
```bash
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw allow 8000/tcp  # Coolify dashboard
ufw enable
```

Confirm with `y` when prompted.

---

## Task 3: Install Coolify

**Time: ~10 minutes**

🖥️ **Step 1: Run the Coolify installer**

```bash
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
```

This installs Docker, Docker Compose, and Coolify itself. It takes 3–5 minutes.

🖥️ **Step 2: Verify Coolify is running**

```bash
docker ps | grep coolify
```

You should see several `coolify` containers running.

🌐 **Step 3: Open the Coolify dashboard**

In your browser, go to: `http://YOUR_SERVER_IP:8000`

You should see the Coolify setup screen.

---

## Task 4: Configure Coolify

**Time: ~10 minutes**

🌐 **Step 1: Create the admin account**

On the Coolify setup screen:
- Enter your email and a strong password
- Click **Register**

🌐 **Step 2: Initial instance setup**

Coolify will prompt you to configure the instance:
- **Instance Domain:** Enter the domain you want for the Coolify dashboard itself (e.g., `coolify.yourdomain.com`). If you don't have a domain yet, you can skip this for now and use the IP.
- **Let it configure the Traefik reverse proxy** — accept the defaults.

🌐 **Step 3: Connect to the local server**

Coolify will ask you to connect to a server. Choose **localhost** (the server Coolify is running on). This connects Coolify to the Docker socket on the same machine.

---

## Task 5: Connect the GitHub repository

**Time: ~5 minutes**

🌐 **Step 1: Go to Sources → GitHub**

In Coolify sidebar: **Sources → GitHub → Add**.

🌐 **Step 2: Create a GitHub App**

Coolify will guide you through creating a private GitHub App on your account that gives it read access to repositories. Follow the prompts — it involves:
- Clicking through to GitHub
- Installing the app on your account
- Selecting which repositories it can access (select `booking-system` and any future repos)

🌐 **Step 3: Verify the connection**

After completing the GitHub App setup, you should be able to browse your repositories from within Coolify.

---

## Task 6: Update Google OAuth redirect URI

**⚠️ Do this BEFORE deploying the app — the app won't work until this is updated.**

You need to know what domain the booking app will live at before completing this step. Decide on the domain now (e.g., `booking.yourdomain.com`).

🌐 **Step 1: Open Google Cloud Console**

Go to https://console.cloud.google.com → **APIs & Services → Credentials**.

🌐 **Step 2: Edit the OAuth client**

Find the OAuth 2.0 client for the booking app → click the edit (pencil) icon.

🌐 **Step 3: Update the redirect URI**

Under **Authorized redirect URIs**, add:
```
https://booking.yourdomain.com/admin/google/callback
```

Remove the old Fly.io redirect URI if present:
```
https://booking-system-fragrant-water-2550.fly.dev/admin/google/callback
```

Save.

---

## Task 7: Create the booking app in Coolify

**Time: ~10 minutes**

🌐 **Step 1: Create a new Project**

Coolify sidebar → **Projects → New Project** → name it `booking-system`.

🌐 **Step 2: Add a new Resource**

Inside the project → **New Resource → Application**.

🌐 **Step 3: Choose source**

Select **GitHub** → select your `booking-system` repository → branch `master`.

🌐 **Step 4: Configure build settings**

Coolify should detect the `Dockerfile` automatically. Confirm:
- **Build Pack:** Dockerfile
- **Port:** `8080`
- **Base Directory:** `/` (root of the repo)

🌐 **Step 5: Set the domain**

Under **Domains**, add:
```
https://booking.yourdomain.com
```

Coolify will handle the Let's Encrypt certificate automatically.

---

## Task 8: Configure environment variables

🌐 In the app settings → **Environment Variables**, add each of the following:

| Variable | Value |
|---|---|
| `DATABASE_URL` | `sqlite:////data/booking.db` (4 slashes — absolute path) |
| `SECRET_KEY` | Generate a random string: `openssl rand -hex 32` |
| `GOOGLE_CLIENT_ID` | Your Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Your Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | `https://booking.yourdomain.com/admin/google/callback` |
| `RESEND_API_KEY` | Your Resend API key |
| `FROM_EMAIL` | The email address notifications send from |
| `TIMEZONE` | `America/New_York` (or your timezone) |

💻 **Generate SECRET_KEY locally:**
```bash
openssl rand -hex 32
```

---

## Task 9: Configure the persistent volume

The SQLite database must survive container restarts. Coolify manages this as a Docker volume.

🌐 **Step 1: Go to Storages tab**

In the app settings → **Storages → Add**.

🌐 **Step 2: Add the volume**

- **Source (host path):** `/data/booking-system` (a directory on the host VPS)
- **Destination (container path):** `/data`
- Click Save.

This maps the host directory `/data/booking-system` to `/data` inside the container, which is where the app writes `booking.db`.

🖥️ **Step 3: Create the host directory**

SSH into the server and create it:
```bash
mkdir -p /data/booking-system
```

---

## Task 10: Deploy and verify

🌐 **Step 1: Trigger the first deploy**

In Coolify → the booking app → click **Deploy**.

🌐 **Step 2: Watch the build log**

Coolify shows a live build log. Watch for:
- `Successfully built` — Docker image built OK
- `Container started` — app is running

If the build fails, read the error in the log and fix it before continuing.

🌐 **Step 3: Check the health endpoint**

Once deployed, visit:
```
https://booking.yourdomain.com/health
```

Expected response:
```json
{"status": "ok"}
```

🌐 **Step 4: Check the booking page**

Visit `https://booking.yourdomain.com/book` — you should see the booking UI.

🌐 **Step 5: Check the admin setup**

Visit `https://booking.yourdomain.com/admin/setup` on first run to set your admin password.

---

## Task 11: Configure DNS

**Only do this after Step 10 verifies the app is working.**

🌐 **Step 1: Add a DNS A record**

In your DNS provider (Cloudflare or wherever your domain is managed):

| Type | Name | Value | Proxy |
|---|---|---|---|
| A | `booking` | `YOUR_SERVER_IP` | DNS only (grey cloud) at first |

Wait 1–5 minutes for DNS to propagate.

🌐 **Step 2: Verify DNS propagation**

```bash
dig booking.yourdomain.com +short
```

Should return `YOUR_SERVER_IP`.

🌐 **Step 3: Enable Cloudflare proxy (if using Cloudflare)**

Once working, you can turn the Cloudflare proxy back on (orange cloud) for DDoS protection. Note: if you do this, set SSL/TLS mode to **Full (strict)** in Cloudflare.

---

## Task 12: Set up automatic deploys

🌐 **Step 1: Enable GitHub webhook in Coolify**

In the app settings → **Webhook** tab → copy the webhook URL.

🌐 **Step 2: Add webhook to GitHub**

In GitHub → `booking-system` repo → **Settings → Webhooks → Add webhook**:
- **Payload URL:** paste the Coolify webhook URL
- **Content type:** `application/json`
- **Trigger:** `Just the push event`
- Click **Add webhook**

Now every `git push origin master` will trigger an automatic redeploy in Coolify.

💻 **Step 3: Test it**

```bash
git -C /home/devon/Projects/BookingAssistant push origin master
```

Watch Coolify — a new deploy should start within a few seconds.

---

## Task 13: Decommission Fly.io (optional)

Once the new deployment is stable for a day or two:

💻 **Step 1: Destroy the Fly.io app**
```bash
~/.fly/bin/fly apps destroy booking-system-fragrant-water-2550
```

💻 **Step 2: Delete the Fly.io volume**
```bash
~/.fly/bin/fly volumes list --app booking-system-fragrant-water-2550
~/.fly/bin/fly volumes destroy <volume-id>
```

This stops all Fly.io billing immediately.

---

## Adding future apps

For each new app, the incremental work is just Tasks 7–10:
1. Create a new Coolify resource pointing at the new GitHub repo
2. Set environment variables
3. Configure volume if needed
4. Deploy

No new VPS needed until the current one runs out of resources (which for small apps is a long time on a 4GB RAM machine).

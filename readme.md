Central MCP Skills Manager

Central MCP Skills Manager hosts Agent Skills (SKILL.md) from multiple GitHub repositories in a single Docker stack. It combines a lightweight Web UI for repository and profile management with a remote Model Context Protocol (MCP) server for AI clients such as Cline, Roo Code, and OpenCode.

Features

Web UI Management: Add, sync, search, and remove GitHub skill repositories directly from your browser without editing docker-compose.yml.

Framework Profiles: Save skill presets per framework (e.g., Frappe, PHP/Laravel/CI, WordPress, Docker) and switch active skills with one click.

Always-On (Umum) Skills: Pin general-purpose skills so they remain active across all profiles.

Background Auto-Sync: Automatically pulls repository updates on a configurable interval (SYNC_INTERVAL).

Rich MCP Tools:

list_skills: Returns active skills along with name and description parsed from YAML frontmatter.

get_skill: Reads SKILL.md and lists any supporting files inside the skill directory.

get_skill_file: Reads supporting reference or script files (e.g., references/api.md).

Multi-Architecture Docker: Built automatically via GitHub Actions for linux/amd64 and linux/arm64.

Architecture

The stack runs two containers from the same Docker image sharing one persistent volume (/data):

[ Local Machine ]                          [ VPS (Docker + Nginx) ]

VS Code + Cline  ──(LLM Prompts)────────►  9router / LLM API
       │
       └───────────(MCP Tools /mcp)─────►  central-mcp (Port 8000, read-only /data)
                                                 ▲
Browser          ──(Web UI /)───────────►  skills-ui   (Port 8001, read-write /data)


Quick Start (Docker Compose / Arcane)

Create a new stack in Arcane (or create a docker-compose.yml file on your VPS).

Paste the configuration below and replace username/repo with your GitHub Container Registry image path:

services:
  skills-ui:
    image: ghcr.io/username/repo:latest
    container_name: mcp-skills-ui
    restart: unless-stopped
    command: uvicorn ui:app --host 0.0.0.0 --port 8001
    environment:
      # Auto-sync interval in seconds (21600 = 6 hours, 0 = disabled)
      - SYNC_INTERVAL=21600
    ports:
      - "127.0.0.1:8001:8001"
    volumes:
      - skills_data:/data

  central-mcp:
    image: ghcr.io/username/repo:latest
    container_name: central-mcp
    restart: unless-stopped
    command: python server.py
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - skills_data:/data:ro

volumes:
  skills_data:


Deploy the stack:

docker compose up -d


Nginx Reverse Proxy

Expose both the Web UI and the MCP endpoint under one SSL-enabled subdomain. Protect the Web UI with HTTP Basic Auth and protect /mcp with an API key header.

Create a password file for the Web UI:

sudo htpasswd -c /etc/nginx/.htpasswd_mcp admin


Add the following location blocks to your Nginx site configuration:

# 1. Web UI (Protected by Basic Auth)
location / {
    auth_basic "MCP Skills Manager";
    auth_basic_user_file /etc/nginx/.htpasswd_mcp;

    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

# 2. MCP Endpoint (Protected by API Key)
location /mcp {
    if ($http_x_api_key != "YOUR_SECRET_API_KEY") {
        return 401;
    }

    proxy_pass http://127.0.0.1:8000/mcp;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding on;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}


Test and reload Nginx:

sudo nginx -t && sudo systemctl reload nginx


Note: If central-mcp logs show a 421 Misdirected Request or Invalid Host header error due to FastMCP DNS rebinding protection, change proxy_set_header Host $host; inside location /mcp to proxy_set_header Host 127.0.0.1:8000;.

Client Configuration (Cline / OpenCode)

Open your AI client's MCP configuration file (e.g., cline_mcp_settings.json).

Add the central-skills server:

{
  "mcpServers": {
    "central-skills": {
      "url": "https://mcp.yourdomain.com/mcp",
      "headers": {
        "X-API-Key": "YOUR_SECRET_API_KEY"
      }
    }
  }
}


Add this instruction to your AI client's Custom Instructions:

Before starting a framework-specific or architectural task, call list_skills from central-skills. Read any relevant skill with get_skill (and supporting files with get_skill_file if listed) before writing code.

Daily Usage

Open https://mcp.yourdomain.com in your browser and log in.

Paste a GitHub repository URL containing SKILL.md files and click + Tambah & Clone.

Click Pin Umum on general skills that should stay active across every project.

Configure framework profiles:

Click Nonaktifkan Semua (general pinned skills remain active).

Enable skills for a specific framework (e.g., Frappe).

Click Update on the matching profile.

Click Pakai on any profile whenever you switch projects. Changes apply immediately to Cline without restarting containers.

Repository Structure

central-mcp/
├── .github/
│   └── workflows/
│       └── docker.yml      # Multi-arch build & push to GHCR
├── .dockerignore
├── Dockerfile
├── README.md
├── requirements.txt
├── server.py               # FastMCP server (Port 8000)
└── ui.py                   # FastAPI Web UI & Git sync (Port 8001)


Build from Source

Pushing to the main branch automatically triggers .github/workflows/docker.yml, which builds linux/amd64 and linux/arm64 images and publishes them to GitHub Container Registry (ghcr.io).
